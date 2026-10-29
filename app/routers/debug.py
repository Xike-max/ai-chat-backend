"""调试路由（知识点 14）：只在 settings.DEBUG=True 时挂载。

用途：验证"日志格式 + trace_id 贯穿"这一套是否真的生效，而不是靠读代码猜。
线上不挂载（见 app/main.py 的条件），因为调试接口会暴露内部结构，
而且能主动制造错误日志、污染告警。

设计上刻意做成"一次请求打四条不同级别的日志"：
一条 curl 就能同时看到 DEBUG / INFO / WARNING / ERROR 的格式差异，
以及它们是否都带**同一个** trace_id。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.deps import get_current_user
from app.logging_conf import get_trace_id, logger

# 除了"只在 DEBUG 且非生产挂载"，再加一道鉴权：调试接口会污染日志与告警，
# 匿名可打等于把日志系统交给公网支配（一条循环脚本就能刷爆采集配额）。
router = APIRouter(
    prefix="/debug",
    tags=["调试"],
    dependencies=[Depends(get_current_user)],
)


@router.get(
    "/log-test",
    summary="打四条不同级别的日志，验证格式与 trace_id",
    response_description="返回本次请求的 trace_id 与已输出的日志级别",
)
async def log_test() -> dict[str, object]:
    """故意输出 debug / info / warning / error 四条日志。

    Returns:
        dict[str, object]: trace_id、四个级别，以及"当前生效级别"的提示。

    Note:
        返回体里带 trace_id，是为了让人**不用翻日志**就能先确认中间件生效了：
        响应头里的 X-Trace-Id 和这个值必须一致。

        如果 LOG_LEVEL=INFO，DEBUG 那条会被丢弃 —— 这是正确行为，不是 bug。
        想看到全部四条，把环境变量改成 LOG_LEVEL=DEBUG 再重启。
    """
    # 1. DEBUG：最啰嗦的一级，只在本地排障时开。生产开它是事故源：
    #    一是日志量爆炸（磁盘/费用），二是可能把参数值、内部状态写进日志。
    #    注意这里**没有**手写 trace_id —— 它由 Filter 自动注入，这正是要验证的点。
    logger.debug("调试日志 | 这条只在 LOG_LEVEL=DEBUG 时出现")

    # 2. INFO：正常业务节点。请求链路里最常打的一级。
    logger.info("信息日志 | 正常的业务节点，例如请求处理完成")

    # 3. WARNING：预期内的失败 / 已自动恢复。不应该触发半夜告警。
    logger.warning("警告日志 | 预期内的失败，例如下游超时后重试成功")

    # 4. ERROR：需要人介入。注意 exc_info=False —— 这里没有异常对象，
    #    硬塞 exc_info=True 会打出一段 NoneType 的假堆栈，反而误导。
    logger.error("错误日志 | 需要人介入的失败，例如写库失败（此条为演示，非真实故障）")

    return {
        "trace_id": get_trace_id(),
        "levels": ["DEBUG", "INFO", "WARNING", "ERROR"],
        "note": "DEBUG 那条是否出现在日志里，取决于 LOG_LEVEL；响应头 X-Trace-Id 应与 trace_id 一致",
    }
