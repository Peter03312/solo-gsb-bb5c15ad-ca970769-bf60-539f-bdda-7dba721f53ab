"""ORM 模型：申请本体 + 修订事件流（批准/撤销审计日志）。"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Float, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base

# 申请状态
STATUS_PENDING = "pending"      # 待审
STATUS_APPROVED = "approved"    # 已批准（有效）
STATUS_REVOKED = "revoked"      # 已撤销（不再参与后续裁决，但记录保留）

# 事件类型
EVENT_APPROVED = "approved"
EVENT_REVOKED = "revoked"


class Application(Base):
    __tablename__ = "applications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # 平面坐标中的覆盖圆：圆心 (x, y) 与半径（同一长度单位，半径须 > 0）
    center_x: Mapped[float] = mapped_column(Float, nullable=False)
    center_y: Mapped[float] = mapped_column(Float, nullable=False)
    radius: Mapped[float] = mapped_column(Float, nullable=False)

    # 频率闭区间 [freq_low_mhz, freq_high_mhz]（MHz）
    freq_low_mhz: Mapped[float] = mapped_column(Float, nullable=False)
    freq_high_mhz: Mapped[float] = mapped_column(Float, nullable=False)

    # 本申请要求的保护带宽（MHz，非负）；比较时取两申请的较大值
    guard_band_mhz: Mapped[float] = mapped_column(Float, nullable=False)

    # UTC 左闭右开时段 [start_utc, end_utc)
    start_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    applicant: Mapped[str | None] = mapped_column(String(256), nullable=True)
    purpose: Mapped[str | None] = mapped_column(String(512), nullable=True)

    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=STATUS_PENDING, index=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class EventLog(Base):
    """每次批准 / 撤销产生一条事件；id 即全局单调递增的修订号（revision）。

    即使裁决事务回滚也不会产生记录，因此修订号就是已提交裁决的顺序。
    """

    __tablename__ = "event_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    application_id: Mapped[int] = mapped_column(
        Integer, index=True, nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
