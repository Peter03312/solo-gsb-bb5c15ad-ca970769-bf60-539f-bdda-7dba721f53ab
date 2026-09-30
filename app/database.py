"""数据库引擎、会话与建表/等待逻辑。"""

import logging
import time
from collections.abc import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings

logger = logging.getLogger("radio")

settings = get_settings()

engine: Engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def wait_for_database() -> None:
    """容器编排中 API 可能先于 PostgreSQL 就绪，轮询等待。"""
    last_error: Exception | None = None
    for attempt in range(1, settings.db_connect_retries + 1):
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            logger.info("database is ready")
            return
        except Exception as exc:  # 启动阶段网络/库未就绪均重试
            last_error = exc
            logger.info(
                "waiting for database (attempt %d/%d): %s",
                attempt,
                settings.db_connect_retries,
                exc.__class__.__name__,
            )
            time.sleep(settings.db_connect_retry_interval_seconds)
    raise RuntimeError(f"database not reachable: {last_error}")


def initialize_database() -> None:
    """首次启动自动建表（幂等），实现一键初始化。"""
    from . import models  # noqa: F401  确保模型已注册到 metadata

    Base.metadata.create_all(bind=engine)
