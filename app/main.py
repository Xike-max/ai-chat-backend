"""应用入口：只做装配（日志 → 实例 → 中间件 → 异常处理器 → 路由），不写业务逻辑。"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.config import settings
from app.errors import register_exception_handlers
from app.logging_conf import PROCESS_TIME_HEADER, TRACE_ID_HEADER, setup_logging
from app.middleware import TraceMiddleware
from app.routers import (auth, chat, conversations, debug, demo_deps, demo_errors,
                         health, internal, models, root, users)

# 必须最先调用：否则前几条日志会走 logging 的 lastResort（无格式、写 stderr）
setup_logging(settings.LOG_LEVEL)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """启动/关闭钩子（替代已废弃的 @app.on_event）。"""
    logger.info("AI Chat Backend 启动，版本 %s，环境 %s", __version__, settings.ENV)
    yield
    logger.info("AI Chat Backend 已关闭")


app = FastAPI(
    title=settings.APP_NAME,
    version=__version__,
    description="AI 对话后端：认证、会话持久化、统一错误契约。",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
    expose_headers=[TRACE_ID_HEADER, PROCESS_TIME_HEADER],  # 不暴露则前端读不到 trace_id
)
app.add_middleware(TraceMiddleware)  # 最后添加 = 最外层，预检请求也带 X-Trace-Id

register_exception_handlers(app)  # 必须在挂路由之前

for module in (health, auth, conversations, chat, models, users, demo_deps):
    app.include_router(module.router, prefix="/api/v1")

# 演示/调试路由只在非生产挂载：能稳定触发 500 的接口留在线上是白送错误率
if settings.ENV.lower() not in {"prod", "production"}:
    app.include_router(demo_errors.router, prefix="/api/v1")
    if settings.DEBUG:
        app.include_router(debug.router, prefix="/api/v1")

# 内部接口故意不挂 /api/v1：网关只暴露 /api/v1，/internal 留在内网
app.include_router(internal.router)
app.include_router(root.router)
