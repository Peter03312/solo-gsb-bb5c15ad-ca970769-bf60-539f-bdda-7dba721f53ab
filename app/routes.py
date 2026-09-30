"""HTTP 路由：申请、预览、批准（带修订号乐观裁决）、撤销、修订历史。"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from . import service
from .config import get_settings
from .database import get_db
from .models import (
    EVENT_APPROVED,
    EVENT_REVOKED,
    STATUS_APPROVED,
    STATUS_PENDING,
    STATUS_REVOKED,
    Application,
    EventLog,
)
from .schemas import (
    ApplicationCreate,
    ApplicationOut,
    ApproveOut,
    ApproveRequest,
    EventOut,
    PreviewOut,
    RevisionListOut,
    RevisionOut,
    RevokeOut,
)

router = APIRouter(prefix="/api")

_LOCK = f"SELECT pg_advisory_lock({service.ADVISORY_LOCK_KEY})"
_UNLOCK = f"SELECT pg_advisory_unlock({service.ADVISORY_LOCK_KEY})"
_XACT_LOCK = f"SELECT pg_advisory_xact_lock({service.ADVISORY_LOCK_KEY})"


def _error(status_code: int, code: str, message: str, **extra) -> JSONResponse:
    body: dict = {"error": code, "message": message}
    body.update(extra)
    return JSONResponse(status_code=status_code, content=body)


def _get_application(db: Session, application_id: int) -> Application | None:
    return db.get(Application, application_id)


def _to_event_out(event: EventLog) -> EventOut:
    return EventOut(
        revision=event.id,
        application_id=event.application_id,
        event_type=event.event_type,
        created_at=event.created_at,
    )


@router.post(
    "/applications",
    response_model=ApplicationOut,
    status_code=status.HTTP_201_CREATED,
    tags=["applications"],
)
def create_application(
    payload: ApplicationCreate,
    db: Session = Depends(get_db),
):
    """提交临时活动无线电使用申请。

    半径非正、频段下界大于上界、起点不早于终点会在请求校验阶段
    整笔拒收（HTTP 422），不产生任何记录。
    """
    settings = get_settings()
    app = Application(
        center_x=payload.center_x,
        center_y=payload.center_y,
        radius=payload.radius,
        freq_low_mhz=payload.freq_low_mhz,
        freq_high_mhz=payload.freq_high_mhz,
        guard_band_mhz=(
            payload.guard_band_mhz
            if payload.guard_band_mhz is not None
            else settings.guard_band_mhz
        ),
        start_utc=payload.start_utc,
        end_utc=payload.end_utc,
        applicant=payload.applicant,
        purpose=payload.purpose,
        status=STATUS_PENDING,
    )
    db.add(app)
    db.commit()
    db.refresh(app)
    return app


@router.get(
    "/applications", response_model=list[ApplicationOut], tags=["applications"]
)
def list_applications(db: Session = Depends(get_db)):
    return list(db.scalars(select(Application).order_by(Application.id.desc())))


@router.get(
    "/applications/{application_id}",
    response_model=ApplicationOut,
    tags=["applications"],
)
def get_application(application_id: int, db: Session = Depends(get_db)):
    app = _get_application(db, application_id)
    if app is None:
        return _error(404, "NOT_FOUND", f"application {application_id} not found")
    return app


@router.get(
    "/applications/{application_id}/preview",
    response_model=PreviewOut,
    tags=["arbitration"],
)
def preview_application(
    application_id: int,
    db: Session = Depends(get_db),
):
    """待审申请预览：返回冲突对象、触发条件及当前修订号。

    预览在与裁决相同的咨询锁下读取，保证看到的修订号与有效集合一致。
    """
    app = _get_application(db, application_id)
    if app is None:
        return _error(404, "NOT_FOUND", f"application {application_id} not found")
    if app.status != STATUS_PENDING:
        return _error(
            409,
            "INVALID_STATE",
            f"application is already {app.status}; only pending applications can be previewed",
            status=app.status,
        )

    db.execute(text(_LOCK))
    try:
        revision = service.current_revision(db)
        conflicts = service.build_conflicts(app, service.effective_approvals(db))
    finally:
        db.execute(text(_UNLOCK))

    return PreviewOut(
        application_id=app.id,
        current_revision=revision,
        has_conflict=bool(conflicts),
        conflicts=conflicts,
    )


@router.post(
    "/applications/{application_id}/approve",
    response_model=ApproveOut,
    tags=["arbitration"],
)
def approve_application(
    application_id: int,
    payload: ApproveRequest,
    db: Session = Depends(get_db),
):
    """批准申请，必须携带预览得到的修订号并重新裁决。

    - 修订号过期（期间有其他批准/撤销提交）-> 409 STALE_REVISION；
    - 重新裁决发现与当前有效集合冲突 -> 409 CONFLICT；
      所有裁决在同一事务级咨询锁下串行，因此修订号匹配时有效集合
      必然与预览一致；重新裁决是对规则正确性的再次保障；
    - 已处于终态 -> 409 INVALID_STATE。
    """
    app = _get_application(db, application_id)
    if app is None:
        return _error(404, "NOT_FOUND", f"application {application_id} not found")

    # 关键区开始：事务级咨询锁，提交/回滚时自动释放
    db.execute(text(_XACT_LOCK))

    revision_now = service.current_revision(db)
    if payload.expected_revision != revision_now:
        db.rollback()
        return _error(
            409,
            "STALE_REVISION",
            "expected revision is outdated; re-fetch preview and arbitrate again",
            expected_revision=payload.expected_revision,
            current_revision=revision_now,
        )

    # 锁内复查，防止对象在加锁前已被并发改变
    db.refresh(app)
    if app.status != STATUS_PENDING:
        db.rollback()
        return _error(
            409,
            "INVALID_STATE",
            f"application is already {app.status}; cannot approve",
            status=app.status,
        )

    conflicts = service.build_conflicts(app, service.effective_approvals(db))
    if conflicts:
        db.rollback()
        return _error(
            409,
            "CONFLICT",
            "application conflicts with currently approved applications; cannot occupy spectrum",
            application_id=app.id,
            conflicts=[c.model_dump(mode="json") for c in conflicts],
        )

    event = EventLog(application_id=app.id, event_type=EVENT_APPROVED)
    db.add(event)
    app.status = STATUS_APPROVED
    app.decided_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(event)

    return ApproveOut(application_id=app.id, status=STATUS_APPROVED, revision=event.id)


@router.post(
    "/applications/{application_id}/revoke",
    response_model=RevokeOut,
    tags=["arbitration"],
)
def revoke_application(application_id: int, db: Session = Depends(get_db)):
    """撤销已批准申请。

    撤销仅从后续裁决所用的有效集合中移除该申请并产生一条修订记录；
    历史修订号仍可查询到当时的批准与撤销。
    """
    app = _get_application(db, application_id)
    if app is None:
        return _error(404, "NOT_FOUND", f"application {application_id} not found")
    if app.status != STATUS_APPROVED:
        return _error(
            409,
            "INVALID_STATE",
            f"only approved applications can be revoked; current status is {app.status}",
            status=app.status,
        )

    db.execute(text(_XACT_LOCK))
    # 锁内复查状态，防止与并发裁决竞争
    db.refresh(app)
    if app.status != STATUS_APPROVED:
        db.rollback()
        return _error(
            409,
            "INVALID_STATE",
            f"application is {app.status}; cannot revoke",
            status=app.status,
        )

    event = EventLog(application_id=app.id, event_type=EVENT_REVOKED)
    db.add(event)
    app.status = STATUS_REVOKED
    app.decided_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(event)

    return RevokeOut(application_id=app.id, status=STATUS_REVOKED, revision=event.id)


@router.get("/revisions", response_model=RevisionListOut, tags=["history"])
def list_revisions(db: Session = Depends(get_db)):
    """当前修订号与全部批准/撤销记录（修订流）。"""
    db.execute(text(_LOCK))
    try:
        revision = service.current_revision(db)
        events = list(db.scalars(select(EventLog).order_by(EventLog.id)))
    finally:
        db.execute(text(_UNLOCK))
    return RevisionListOut(
        current_revision=revision, events=[_to_event_out(e) for e in events]
    )


@router.get("/revisions/{revision}", response_model=RevisionOut, tags=["history"])
def get_revision(revision: int, db: Session = Depends(get_db)):
    """按修订号查询：重放事件流还原当时的有效批准集合及批准/撤销记录。"""
    if revision < 0:
        return _error(404, "REVISION_NOT_FOUND", f"revision {revision} does not exist")

    latest = service.current_revision(db)
    if revision > latest:
        return _error(
            404,
            "REVISION_NOT_FOUND",
            f"revision {revision} does not exist; current revision is {latest}",
            current_revision=latest,
        )

    approved, events = service.snapshot_at(db, revision)
    # 每个申请至多有一条批准事件（撤销后不可再次批准），可用事件还原当时状态
    approved_at = {
        e.application_id: e.created_at for e in events if e.event_type == EVENT_APPROVED
    }
    out: list[ApplicationOut] = []
    for app in approved:  # live at that revision => 当时状态为 approved
        snapshot = ApplicationOut.model_validate(app)
        snapshot.status = STATUS_APPROVED
        snapshot.decided_at = approved_at.get(app.id)
        out.append(snapshot)
    return RevisionOut(
        revision=revision,
        approved=out,
        events=[_to_event_out(e) for e in events],
    )
