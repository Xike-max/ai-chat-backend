"""应用入口：创建 FastAPI 实例、注册路由与中间件。

路由分三类：
- /api/v1/*：对外契约（浏览器、App）
- /internal/*：机器之间的私路（运维脚本、定时任务），靠 X-API-Key 鉴权
- /：服务入口，给出常用地址，避免浏览器访问根路径只看到 404
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.config import settings
from app.errors import register_exception_handlers
from app.logging_conf import setup_logging
from app.routers import (
    auth,
    conversations,
    demo_deps,
    demo_errors,
    health,
    internal,
    models,
    users,
)

# 必须在任何 logger 使用之前调用，否则前几条日志会走 lastResort 处理器
# （只输出到 stderr、不带时间格式）
setup_logging()

logger = logging.getLogger(__name__)

# 前端联调地址：Next.js / Vite 默认 3000，Streamlit 默认 8501
ALLOWED_ORIGINS: list[str] = [
    "http://localhost:3000",
    "http://localhost:8501",
]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """应用生命周期钩子：启动/关闭逻辑统一放在这里，不使用已废弃的事件装饰器。

    Args:
        app: 当前 FastAPI 实例。

    Yields:
        None: yield 之前是启动逻辑，之后是关闭逻辑。
    """
    logger.info("AI Chat Backend 启动完成，版本 %s", __version__)
    yield
    logger.info("AI Chat Backend 已关闭")


app = FastAPI(
    title="AI Chat Backend",
    version=__version__,
    description="面向 AI 对话场景的后端服务，提供 JWT 认证、健康检查、会话与模型白名单接口。",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 统一错误格式：四个处理器一次性挂上（顺序无关，见 app/errors.py）。
# 必须在这里、在挂路由之前调用 —— 越早挂上，越不会漏掉某个模块抛出的异常。
register_exception_handlers(app)

# tags 用中文，Swagger /docs 里会按中文分组展示
app.include_router(health.router, prefix="/api/v1", tags=["健康检查"])
# 认证路由必须最先挂：/auth/token 的完整路径是 Swagger 的 tokenUrl，
# 两者写的是同一个字符串（/api/v1/auth/token），改前缀时务必一起改
app.include_router(auth.router, prefix="/api/v1")
app.include_router(conversations.router, prefix="/api/v1")
app.include_router(models.router, prefix="/api/v1")
app.include_router(users.router, prefix="/api/v1")
app.include_router(demo_deps.router, prefix="/api/v1")

# 故意出错的演示路由只在非生产环境挂载：
# 一个能稳定触发 500 的接口留在线上，等于白送攻击者一个错误率制造器
if settings.ENV.lower() not in {"prod", "production"}:
    app.include_router(demo_errors.router, prefix="/api/v1")

# 内部接口**故意不挂 /api/v1**：/api/v1/* 是对外契约（浏览器、App），
# /internal/* 是机器之间的私路（运维脚本、定时任务）。两者分开后，
# 网关可以只把 /api/v1 暴露到公网，/internal 留在内网，靠网络边界先挡一层。
# 它的鉴权由 router 自己的 dependencies=[Depends(verify_api_key)] 保证。
app.include_router(internal.router)


@app.get("/", tags=["服务入口"], summary="服务入口")
async def root() -> dict[str, str]:
    """浏览器直接访问根路径时调用，返回服务名、版本与常用入口地址。

    Returns:
        dict[str, str]: 服务信息与 docs / health / openapi 的相对路径。
    """
    return {
        "service": "AI Chat Backend",
        "version": __version__,
        "docs": "/docs",
        "health": "/api/v1/health",
        "openapi": "/openapi.json",
    }
