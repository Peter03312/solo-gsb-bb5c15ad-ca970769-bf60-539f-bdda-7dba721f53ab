"""裁决服务层：修订号、有效集合、冲突明细构造与咨询锁。"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import interference
from .models import (
    EVENT_APPROVED,
    STATUS_APPROVED,
    Application,
    EventLog,
)
from .schemas import ApplicationOut, ConflictDetail

# 裁决串行锁键（pg_advisory_xact_lock，事务结束自动释放）。
# 所有“读取当前修订号/有效集合 + 写入批准或撤销”的关键区都在同一把锁内，
# 从而把乐观号校验与并发裁决原子化。
ADVISORY_LOCK_KEY = 735671001


def current_revision(db: Session) -> int:
    """当前修订号 = 事件表最大 id；尚无事件时为 0。"""
    return db.scalar(select(func.coalesce(func.max(EventLog.id), 0))) or 0


def effective_approvals(db: Session) -> list[Application]:
    """当前参与裁决的有效集合：已批准且未撤销（撤销只改变本集合）。"""
    return list(
        db.scalars(
            select(Application)
            .where(Application.status == STATUS_APPROVED)
            .order_by(Application.id)
        )
    )


def serialize_application(app: Application) -> ApplicationOut:
    return ApplicationOut.model_validate(app)


def build_conflicts(
    candidate: Application, approved: list[Application]
) -> list[ConflictDetail]:
    """对有效批准集合逐一裁决，返回全部冲突对象及触发条件。"""
    conflicts: list[ConflictDetail] = []
    for other in approved:
        trigger = interference.evaluate(candidate, other)
        if trigger is not None:
            conflicts.append(
                ConflictDetail(
                    application=serialize_application(other),
                    time_overlap=trigger.time_overlap,
                    area_overlap=trigger.area_overlap,
                    frequency_overlap=trigger.frequency_overlap,
                    band_gap_mhz=trigger.band_gap_mhz,
                    required_guard_band_mhz=trigger.required_guard_band_mhz,
                    trigger=trigger.reason,
                )
            )
    return conflicts


def snapshot_at(db: Session, revision: int) -> tuple[list[Application], list[EventLog]]:
    """重放截至某修订号的事件流，还原当时的有效批准集合与事件记录。

    撤销只在其记录出现之后生效：即“按修订号查询仍须还原当时的
    批准、撤销记录”。
    """
    events = list(
        db.scalars(
            select(EventLog)
            .where(EventLog.id <= revision)
            .order_by(EventLog.id)
        )
    )
    live_ids: set[int] = set()
    for event in events:
        if event.event_type == EVENT_APPROVED:
            live_ids.add(event.application_id)
        else:  # revoked
            live_ids.discard(event.application_id)

    approved = list(
        db.scalars(
            select(Application)
            .where(Application.id.in_(live_ids))
            .order_by(Application.id)
        )
    ) if live_ids else []
    return approved, events
