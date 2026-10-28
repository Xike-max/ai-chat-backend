"""日志配置（知识点 13）。

统一出口，避免每个模块各自 `print` 或各自 basicConfig：

- 日志格式带时间、级别、模块名，排查时能直接看出"是谁在哪一刻说的"；
- 输出到 stdout 而不是文件：容器/PM2/systemd 会负责收集，
  应用自己写文件会遇到轮转、权限、多副本互相覆盖的一堆问题；
- 只配置一次（main.py 启动时调用 setup_logging），重复调用不会叠加 handler。

级别约定：
- DEBUG   仅本地排障，生产关掉（会给每条 SQL、每个请求刷屏）
- INFO    正常业务节点（启动、关闭、关键状态变更）
- WARNING 可恢复的异常（重试成功、降级）
- ERROR   需要人介入（未捕获异常、下游持续失败）
"""

import logging
import sys

LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# 本应用的根 logger。业务模块用 `from app.logging_conf import logger` 共享它，
# 好处是全局只改一处级别；需要更细粒度时再按模块 getLogger(__name__)。
logger = logging.getLogger("app")


def setup_logging(level: int = logging.INFO) -> None:
    """初始化根 logger（幂等，重复调用不会重复挂 handler）。

    Args:
        level: 根 logger 的级别，默认 INFO。

    Note:
        如果部署环境已经用 `uvicorn --log-config` 或 dictConfig 配过日志，
        basicConfig 会因为"根 logger 已有 handler"而什么都不做 —— 这是期望行为，
        我们不覆盖运维的配置。
    """
    logging.basicConfig(
        level=level,
        format=LOG_FORMAT,
        datefmt=DATE_FORMAT,
        stream=sys.stdout,
    )
    logger.setLevel(level)
