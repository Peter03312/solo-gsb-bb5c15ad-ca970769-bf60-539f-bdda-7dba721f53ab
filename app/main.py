"""FastAPI 入口：启动时等待 PostgreSQL 并自动建表。"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .database import initialize_database, wait_for_database
from .routes import router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    wait_for_database()
    initialize_database()
    yield


app = FastAPI(
    title="临时活动无线电使用申请 API",
    description=(
        "提交含覆盖圆、闭频段、UTC 左闭右开时段的申请；"
        "基于修订号的乐观并发裁决，支持冲突预览、批准、撤销与历史还原。"
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(router)


@app.get("/health", tags=["system"])
def health():
    return {"status": "ok"}
