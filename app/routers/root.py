"""服务入口路由：浏览器直接访问 ``/`` 时给一份指路牌。

为什么单独一个文件而不是塞在 main.py 里：
main.py 的职责只有"装配"（实例、中间件、异常处理器、include_router）。
一旦允许它写第一个路由函数，第二个、第三个就会跟着进来，最后 main.py
又变回那个什么都干的 `views.py`。所以哪怕只是返回一个静态字典，
它也必须有自己的一层。
"""

from __future__ import annotations

from fastapi import APIRouter

from app import __version__

router = APIRouter(tags=["服务入口"])


@router.get("/", summary="服务入口")
async def root() -> dict[str, str]:
    """返回服务名、版本与常用入口地址。

    这个接口不含任何业务数据（不碰数据库、不读用户信息），所以不需要鉴权；
    它的价值是让运维在浏览器里一眼确认"这个端口跑的是哪个服务、文档在哪"。

    Returns:
        dict[str, str]: 服务名、版本、docs / health / openapi 的相对路径。
    """
    return {
        "service": "AI Chat Backend",
        "version": __version__,
        "docs": "/docs",
        "health": "/api/v1/health",
        "openapi": "/openapi.json",
    }
