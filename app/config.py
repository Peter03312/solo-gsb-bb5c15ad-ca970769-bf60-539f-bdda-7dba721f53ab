"""全局配置，全部可通过环境变量覆盖。"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # SQLAlchemy 驱动，psycopg v3 对应 postgresql+psycopg://
    database_url: str = "postgresql+psycopg://radio:radio@db:5432/radio"

    # 默认保护带宽（MHz），申请未显式给出 guard_band_mhz 时使用；必须非负
    guard_band_mhz: float = Field(default=0.0, ge=0.0)

    # 启动时等待数据库就绪的重试参数
    db_connect_retries: int = 30
    db_connect_retry_interval_seconds: float = 2.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
