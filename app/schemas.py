"""请求/响应 Pydantic 模型。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ApplicationCreate(BaseModel):
    # 覆盖圆
    center_x: float
    center_y: float
    radius: float = Field(description="覆盖圆半径，必须 > 0（与坐标同一长度单位）")

    # 频率闭区间，单位 MHz
    freq_low_mhz: float
    freq_high_mhz: float

    # 保护带宽，单位 MHz；省略时使用服务端默认值（环境变量 GUARD_BAND_MHZ）
    guard_band_mhz: float | None = Field(default=None, ge=0.0)

    # UTC 左闭右开时段；接受 ISO-8601，带 Z/偏移；无时区信息时按 UTC 解释
    start_utc: datetime
    end_utc: datetime

    applicant: str | None = None
    purpose: str | None = None

    @field_validator("start_utc", "end_utc")
    @classmethod
    def _ensure_utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            from datetime import timezone

            return v.replace(tzinfo=timezone.utc)
        return v.astimezone(timezone.utc)

    @model_validator(mode="after")
    def _validate_shape(self):
        if self.radius <= 0:
            raise ValueError("radius must be positive")
        if self.freq_low_mhz > self.freq_high_mhz:
            raise ValueError("freq_low_mhz must not be greater than freq_high_mhz")
        if not self.start_utc < self.end_utc:
            raise ValueError("start_utc must be strictly earlier than end_utc")
        return self


class ApplicationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    center_x: float
    center_y: float
    radius: float
    freq_low_mhz: float
    freq_high_mhz: float
    guard_band_mhz: float
    start_utc: datetime
    end_utc: datetime
    applicant: str | None
    purpose: str | None
    status: str
    created_at: datetime
    decided_at: datetime | None


class ConflictDetail(BaseModel):
    application: ApplicationOut
    time_overlap: bool
    area_overlap: bool
    frequency_overlap: bool
    band_gap_mhz: float | None = Field(
        description="频段不相交时的最短间隔(MHz)；频段相交时为 null"
    )
    required_guard_band_mhz: float = Field(description="本次比较采用的保护带宽(MHz)")
    trigger: str = Field(description="band_overlap=频段相交；guard_band=间隔小于保护带宽")


class PreviewOut(BaseModel):
    application_id: int
    current_revision: int
    has_conflict: bool
    conflicts: list[ConflictDetail]


class ApproveRequest(BaseModel):
    expected_revision: int = Field(
        ge=0, description="预览时获得的当前修订号，批准时据此做乐观并发控制"
    )


class ApproveOut(BaseModel):
    application_id: int
    status: str
    revision: int = Field(description="本次批准写入的修订号")


class RevokeOut(BaseModel):
    application_id: int
    status: str
    revision: int


class EventOut(BaseModel):
    revision: int
    application_id: int
    event_type: str
    created_at: datetime


class RevisionOut(BaseModel):
    revision: int
    approved: list[ApplicationOut] = Field(
        description="截至该修订号重放事件流还原出的有效批准集合（已撤销的不含在内）"
    )
    events: list[EventOut] = Field(description="截至该修订号的全部批准/撤销记录")


class RevisionListOut(BaseModel):
    current_revision: int
    events: list[EventOut]
