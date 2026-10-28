"""内部接口（知识点 11）：给运维脚本和其他服务调用，走 API Key 鉴权。

和 /api/v1/* 的区别：

- /api/v1/*   面向浏览器里的最终用户，凭证是 JWT（下一节实现），带用户身份；
- /internal/* 面向机器：运维脚本、定时任务、兄弟服务。凭证是长期 API Key，
  没有"用户"概念，也不该出现在浏览器里。

整个 router 挂 `dependencies=[Depends(verify_api_key)]`，
所以**两个接口的函数签名里都不需要再写一遍** —— 少写一行，也少一个漏掉的机会。
"""

# date 用别名导入：字段名就叫 date（报表口径固定为 date），
# 而 `date: date = Field(...)` 在 Pydantic 2 里会抛
# PydanticUserError: field name clashing with a type annotation ——
# 类体里 date 这个属性先被 FieldInfo 占住，注解再去找 date 类型就找到了 FieldInfo。
# 改别名后字段名保持不变，JSON 里仍是 "date"。
from datetime import date as Date
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app import __version__
from app.database import get_db
from app.deps import verify_api_key

# 注意这里是 router 级依赖：挂在这个 router 上的接口全部自动受保护，
# 新增接口时不需要记得再加一次（每次都靠人记得，迟早会漏一个，那就是内部接口裸奔）。
router = APIRouter(
    prefix="/internal",
    tags=["内部接口"],
    dependencies=[Depends(verify_api_key)],
)


# ============================================================================
# 深度健康检查
# ============================================================================
class DeepHealthResponse(BaseModel):
    """深度健康检查结果。"""

    status: str = Field(description="整体状态：ok 表示各项检查均通过")
    version: str = Field(description="服务版本")
    database: str = Field(description="数据库连通性：ok / error")
    latency_ms: float = Field(description="数据库探测耗时（毫秒）")
    timestamp: datetime = Field(description="检查时间，ISO 8601 带时区")


@router.get(
    "/health/deep",
    response_model=DeepHealthResponse,
    summary="深度健康检查（含数据库）",
)
async def deep_health(db: Session = Depends(get_db)) -> DeepHealthResponse:
    """运维巡检脚本 / 监控系统调用，除进程存活外还真的打一次数据库。

    与 /api/v1/health 的分工：
    - /api/v1/health 是**浅检查**：只回"进程活着"，给负载均衡做存活探针，
      要求极快且不碰外部依赖（数据库抖一下不该让 LB 把所有实例摘掉）；
    - 这里是**深检查**：会执行 `SELECT 1`，数据库不通就返回 503，给巡检脚本和告警用。
    两者故意分开，避免探针把数据库压力放大。

    Args:
        db: 请求级数据库会话（get_db 依赖注入）。

    Returns:
        DeepHealthResponse: 各项检查结果。

    Raises:
        HTTPException: 503，数据库不可用时。
    """
    started = datetime.now(timezone.utc)
    try:
        # 只验证"连得上、能执行"，不查业务表：巡检不该依赖业务数据是否存在
        db.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        # 不要把异常原文返回给调用方：里面常带连接串（含密码）。
        # 详细堆栈交给日志，响应只给结论。
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="数据库不可用，请检查连接配置与数据库状态",
        ) from exc

    elapsed_ms = (datetime.now(timezone.utc) - started).total_seconds() * 1000
    return DeepHealthResponse(
        status="ok",
        version=__version__,
        database="ok",
        latency_ms=round(elapsed_ms, 2),
        timestamp=datetime.now(timezone.utc),
    )


# ============================================================================
# 用量上报
# ============================================================================
class UsageReportIn(BaseModel):
    """用量上报请求体。

    字段名用统计口径的通用叫法（date / total_tokens / request_count），
    不要自创 tokens_sum 之类 —— 报表脚本会按这三列直接落库。
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "date": "2026-10-27",
                "total_tokens": 128400,
                "request_count": 512,
            }
        }
    )

    date: Date = Field(description="统计日期，格式 YYYY-MM-DD（按 UTC 自然日切分）")
    total_tokens: int = Field(ge=0, description="当日消耗 token 总数")
    request_count: int = Field(ge=0, description="当日请求次数")


class UsageReportOut(BaseModel):
    """用量上报结果。"""

    accepted: bool = Field(description="是否已受理")
    date: Date = Field(description="被受理的统计日期，回显便于对账")
    recorded_at: datetime = Field(description="服务端受理时间，ISO 8601 带时区")


@router.post(
    "/usage/report",
    response_model=UsageReportOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="上报每日 token 用量",
)
async def report_usage(payload: UsageReportIn) -> UsageReportOut:
    """定时任务（如每天 01:00 的 cron）调用，把前一天的用量汇总上报。

    为什么返回 202 而不是 200/201：这里只是"受理"，真正的落库与汇总由后台任务做，
    调用方不必等它完成，也不该因为汇总慢而超时重试。

    幂等约定：同一个 date 重复上报视为覆盖（后到的为准），所以调用方可以安全重试。

    Args:
        payload: 统计日期、token 总数、请求次数。

    Returns:
        UsageReportOut: 受理结果，回显 date 便于对账。
    """
    # TODO: 知识点 9 落库：INSERT ... ON CONFLICT(date) DO UPDATE（幂等覆盖）
    return UsageReportOut(
        accepted=True,
        date=payload.date,
        recorded_at=datetime.now(timezone.utc),
    )
