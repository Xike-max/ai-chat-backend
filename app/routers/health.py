"""健康检查路由：供负载均衡、K8s 探针与前端联调使用。"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app import __version__

router = APIRouter()


class HealthResponse(BaseModel):
    """健康检查响应体。"""

    status: str = Field(description="服务状态，正常时为 ok", examples=["ok"])
    version: str = Field(description="服务版本号", examples=["0.1.0"])
    timestamp: datetime = Field(
        description="服务器当前时间，ISO 8601 格式，UTC 时区（末尾 Z 表示 UTC）",
        examples=["2026-10-27T14:45:55.123456Z"],
    )


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="健康检查",
    description="返回服务存活状态、版本号与当前 UTC 时间。",
)
async def health_check() -> HealthResponse:
    """检查服务是否存活。

    Returns:
        HealthResponse: 含 status、version 与 timezone-aware 的 timestamp。
    """
    return HealthResponse(
        status="ok",
        version=__version__,
        timestamp=datetime.now(timezone.utc),
    )
