"""日志配置（知识点 13、14）。

统一出口，避免每个模块各自 `print` 或各自 basicConfig：

- 输出到 **stdout** 而不是文件：容器里由 Docker / 日志采集器负责收集与轮转
  （12-factor 的 "logs as event streams"）。应用自己写文件会遇到轮转、权限、
  多副本互相覆盖、容器销毁后日志丢失一堆问题；
- 用 `dictConfig` 而不是 `basicConfig`：前者能声明 handler / formatter / filter
  的组合关系，后者只能配一个 formatter 打天下，加第二个输出目标就得改代码；
- 只配置一次（main.py 启动时调用 setup_logging），重复调用会整体重建、不会叠加。

**trace_id 的注入方式**（本文件最核心的设计）
------------------------------------------------------------------
trace_id 存在 `contextvars.ContextVar` 里，而不是作为参数一层层往下传：

    set_trace_id("abc")  ->  这条请求链路上**所有** logger 输出自动带上 abc

如果改成在每个 log 调用里手写 `logger.info("... trace_id=%s", tid)`，
那么：漏写一处 = 那条日志查不到；写错变量 = 排查时对着错误的 id 白找；
换异步任务 = 参数传丢了。用 Filter 从 contextvar 取值，是"配一次、全局生效"，
业务代码完全不需要知道 trace_id 的存在。

为什么是 contextvar 而不是 threading.local：FastAPI 是异步的，一个线程里
会并发跑成百上千个请求，threading.local 会被互相污染（串台）；
contextvar 是"协程级"的，每个请求任务各有独立副本，asyncio 切换时自动隔离。
------------------------------------------------------------------

级别约定见文件末尾的《日志级别使用规范》。
"""

from __future__ import annotations

import json
import logging
import logging.config
import sys
from contextvars import ContextVar, Token
from datetime import datetime
from typing import Any

from app.config import settings

# 两个自定义响应头。放在这里而不是中间件里，是因为 errors.py 生成 500 响应时
# 也要用到它们（500 由最外层 ServerErrorMiddleware 产出，不经过我们的中间件）。
TRACE_ID_HEADER = "X-Trace-Id"
PROCESS_TIME_HEADER = "X-Process-Time"

#: 当前请求的 trace_id。默认值 "-" 是给"还没有请求上下文"的日志用的，
#: 比如模块导入期、lifespan 启动/关闭日志 —— 它们本来就不属于任何请求。
_trace_id_var: ContextVar[str] = ContextVar("trace_id", default="-")

#: 标记"这条请求的异常已经由兜底处理器连堆栈一起记录过了"。
#: 为什么需要它：Starlette 的 ServerErrorMiddleware 在发完 500 响应后**仍会把
#: 异常重新抛出**，uvicorn 接住后再打一条 "Exception in ASGI application" +
#: 同一份完整堆栈。结果是一次 500 在日志里出现两份栈，其中一份还没有 trace_id。
#: 下面那个 Filter 靠这个标记把重复的那份丢掉（只在确实已记录过时才丢）。
_exc_already_logged: ContextVar[bool] = ContextVar("exc_already_logged", default=False)

#: 本应用的根 logger。业务模块用 `from app.logging_conf import logger` 共享它，
#: 好处是全局只改一处级别；需要更细粒度时再按模块 getLogger(__name__)。
logger = logging.getLogger("app")

#: 请求访问日志专用 logger。名字带 .access 是为了能单独调级别或单独关掉
#: （比如压测时不想被访问日志刷屏：logging.getLogger("app.access").setLevel("WARNING")）。
access_logger = logging.getLogger("app.access")


# ============================================================================
# 1. trace_id 的读写接口
# ============================================================================
def get_trace_id() -> str:
    """取当前上下文的 trace_id。

    Returns:
        str: 当前请求的 trace_id；不在请求上下文中时返回 "-"。
    """
    return _trace_id_var.get()


def set_trace_id(trace_id: str) -> Token[str]:
    """把 trace_id 写入当前上下文，返回可用于回滚的 token。

    Args:
        trace_id: 本次请求的追踪 ID。

    Returns:
        Token[str]: 必须传给 reset_trace_id 的句柄。

    Note:
        一定要成对使用（set / reset）。只 set 不 reset 的话，在复用线程或
        复用任务池的场景下，上一个请求的 trace_id 会残留到下一个请求上，
        日志就会出现"两个请求共用同一个 id"的假象 —— 排查时会以为是串台 bug。
    """
    return _trace_id_var.set(trace_id)


def reset_trace_id(token: Token[str]) -> None:
    """恢复 set_trace_id 之前的上下文值。

    Args:
        token: set_trace_id 的返回值。
    """
    _trace_id_var.reset(token)


def mark_exception_logged() -> None:
    """标记本次请求的异常已被兜底处理器完整记录（含堆栈）。"""
    _exc_already_logged.set(True)


def is_exception_logged() -> bool:
    """本次请求的异常是否已被记录。

    Returns:
        bool: 已记录返回 True。
    """
    return _exc_already_logged.get()


# ============================================================================
# 2. Filter：把 contextvar 里的 trace_id 注入到每条 LogRecord
# ============================================================================
class TraceIdFilter(logging.Filter):
    """给每条日志记录挂上 trace_id 属性，供 formatter 的 %(trace_id)s 使用。

    挂在 **handler** 上而不是 logger 上：handler 级过滤器对"所有经由这个
    handler 输出的日志"生效，包括 uvicorn、sqlalchemy 这些第三方 logger
    的记录。挂在某个 logger 上则只对它自己生效，第三方库的日志会缺字段，
    formatter 直接抛 KeyError（表现为日志系统自己崩给你看）。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """写入 record.trace_id，永远返回 True（只加工，不过滤）。

        Args:
            record: 待处理的日志记录。

        Returns:
            bool: 恒为 True，表示这条日志继续输出。
        """
        record.trace_id = _trace_id_var.get()
        return True


class DropDuplicateASGIErrorFilter(logging.Filter):
    """丢掉 uvicorn 对"已经记过堆栈的那次异常"的第二次输出。

    背景：Starlette 的 ServerErrorMiddleware 发完 500 后会把异常重新抛出，
    uvicorn 接住就打一条 `Exception in ASGI application` + 完整堆栈。
    那条日志没有 trace_id，而且和业务日志里的那份完全重复。

    安全边界：只有"本次请求确实已经由兜底处理器记过堆栈"（contextvar 标记）
    时才丢。lifespan、后台任务、连处理器都没跑到的异常不会被这个 Filter 影响 ——
    宁可多打一份，也不能因为遮蔽而丢掉唯一的线索。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """判断是否保留这条记录。

        Args:
            record: 待处理的日志记录。

        Returns:
            bool: 重复的 ASGI 异常记录返回 False（丢弃），其余返回 True。
        """
        if record.name == "uvicorn.error" and record.getMessage().startswith(
            "Exception in ASGI application"
        ):
            return not is_exception_logged()
        return True


# ============================================================================
# 3. Formatter：console（可带颜色）/ json
# ============================================================================
class _ISOTimeFormatter(logging.Formatter):
    """把时间格式化成 ISO 8601（带时区偏移），例如 2026-10-29T19:40:12+08:00。

    不用 `%Y-%m-%d %H:%M:%S` 的原因：没有时区，跨时区排障时要靠猜；
    不用 `%z` 的原因：它输出 `+0800`，少个冒号，不是严格的 ISO 8601，
    ES / Loki 这类系统按 ISO 解析时会失败。
    """

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        """用 record.created 生成 ISO 时间串（本地时区，带偏移）。

        Args:
            record: 日志记录。
            datefmt: 忽略，保留参数以兼容父类签名。

        Returns:
            str: 形如 2026-10-29T19:40:12.123+08:00 的字符串。
        """
        return datetime.fromtimestamp(record.created).astimezone().isoformat(
            timespec="milliseconds"
        )


class ColorFormatter(_ISOTimeFormatter):
    """控制台彩色输出：只给级别上色，其他部分保持原样。

    为什么要自己写而不是装 colorama：只是为了给 5 个级别加 ANSI 转义码，
    多一个运行时依赖不值得。真正要把颜料刷在日志平台上的时候，用的是
    json 格式，颜色反而会污染日志（一堆 \\033[32m 字符串）。
    """

    _COLORS = {
        "DEBUG": "\033[36m",  # 青
        "INFO": "\033[32m",  # 绿
        "WARNING": "\033[33m",  # 黄
        "ERROR": "\033[31m",  # 红
        "CRITICAL": "\033[1;31m",  # 加粗红
    }
    _RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        """在父类格式化前后临时替换 levelname，实现局部着色。

        Args:
            record: 日志记录。

        Returns:
            str: 一行带颜色的日志文本。

        Note:
            先把级别补齐到 8 位再套颜色码。如果反过来（套完颜色再让
            `%(levelname)-8s` 补齐），Python 会把 ANSI 转义码也算进宽度，
            日志列就会歪。
        """
        original = record.levelname
        color = self._COLORS.get(original, "")
        record.levelname = f"{color}{original:<8}{self._RESET}" if color else f"{original:<8}"
        try:
            return super().format(record)
        finally:
            # 必须还原：同一条 record 会被多个 handler 处理，
            # 改坏它会让后面的 handler 输出带颜色码的乱码
            record.levelname = original


class JsonFormatter(_ISOTimeFormatter):
    """结构化 JSON 输出，一行一条，方便 ELK / Loki / CloudWatch 直接索引。

    字段名固定且语义稳定，其中 trace_id 是顶层字段 —— 采集端不用做任何
    正则解析就能按 trace_id 聚合一次请求的全部日志。
    """

    def format(self, record: logging.LogRecord) -> str:
        """把 LogRecord 序列化成单行 JSON。

        Args:
            record: 日志记录。

        Returns:
            str: 单行 JSON 字符串（中文不转义，便于人眼直接看）。
        """
        payload: dict[str, Any] = {
            "time": self.formatTime(record),
            "level": record.levelname,
            "trace_id": getattr(record, "trace_id", "-"),
            "logger": record.name,
            "message": record.getMessage(),
            # 模块名 + 行号：定位到代码位置的最小信息量，比完整 pathname 短
            "module": record.module,
            "line": record.lineno,
        }
        if record.exc_info:
            # 堆栈是多行文本，JSON 里当作一个字符串字段，不展开成数组
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)
        return json.dumps(payload, ensure_ascii=False)


# ============================================================================
# 4. dictConfig 组装
# ============================================================================
CONSOLE_FORMAT = "%(asctime)s | %(levelname)s | %(trace_id)s | %(name)s | %(message)s"


def _use_color() -> bool:
    """判断控制台是否该上色。

    Returns:
        bool: settings.LOG_COLOR 显式配置时以配置为准；否则看 stdout 是不是 TTY。

    Note:
        容器里 stdout 通常不是 TTY，自动上色会把 ANSI 转义码原样写进日志文件，
        `grep` 出来是一堆 `\\033[32m`。所以默认"不是 TTY 就不上色"，
        需要强制开/关时用 LOG_COLOR 覆盖。
    """
    if settings.LOG_COLOR is not None:
        return settings.LOG_COLOR
    try:
        return sys.stdout.isatty()
    except (AttributeError, ValueError):  # 某些被重定向的 stdout 会抛这个
        return False


def build_logging_config(level: str | None = None) -> dict[str, Any]:
    """组装 dictConfig 字典（便于单元测试直接断言，不必真的初始化日志）。

    Args:
        level: 日志级别字符串；不传则用 settings.LOG_LEVEL。

    Returns:
        dict: 可直接交给 logging.config.dictConfig 的配置。
    """
    level = (level or settings.LOG_LEVEL).upper()

    if settings.LOG_JSON:
        formatter_name = "json"
    elif _use_color():
        formatter_name = "console_color"
    else:
        formatter_name = "console"

    return {
        "version": 1,
        # 关键：保留已存在的第三方 logger（sqlalchemy / httpx / uvicorn 子模块），
        # 设成 True 会把它们全部静音 —— 那种"日志突然只剩自己打的"的坑很难查。
        "disable_existing_loggers": False,
        "filters": {
            "trace_id": {"()": "app.logging_conf.TraceIdFilter"},
            "drop_duplicate_asgi_error": {
                "()": "app.logging_conf.DropDuplicateASGIErrorFilter"
            },
        },
        "formatters": {
            "console": {
                "()": "app.logging_conf._ISOTimeFormatter",
                "format": CONSOLE_FORMAT,
            },
            "console_color": {
                "()": "app.logging_conf.ColorFormatter",
                "format": CONSOLE_FORMAT,
            },
            "json": {"()": "app.logging_conf.JsonFormatter"},
        },
        "handlers": {
            # 只配一个输出目标：stdout。stderr 留给"日志系统自己坏了"的报错，
            # 两者混在一起会让 `docker logs` 的时序错乱。
            "console": {
                "class": "logging.StreamHandler",
                "level": level,
                "formatter": formatter_name,
                "filters": ["trace_id", "drop_duplicate_asgi_error"],
                "stream": "ext://sys.stdout",
            },
        },
        "root": {"handlers": ["console"], "level": level},
        "loggers": {
            # 本应用的 logger 全部冒泡到 root，靠 root 的 console handler 输出。
            # 把 propagate 留着 True，是为了让 `caplog` / 上层重新配置时仍然有效。
            "app": {"handlers": [], "level": level, "propagate": True},
            "app.access": {"handlers": [], "level": level, "propagate": True},
            # uvicorn 自带的 handler 会输出一份"没有 trace_id、格式还不一样"的日志，
            # 这里清空它的 handlers 并让它冒泡到 root，格式就和业务日志统一了。
            "uvicorn": {"handlers": [], "level": level, "propagate": True},
            "uvicorn.error": {"handlers": [], "level": level, "propagate": True},
            # 访问日志由 TraceMiddleware 统一打（带 trace_id、耗时、状态码），
            # uvicorn 再来一条就是重复且缺字段，直接关掉。
            "uvicorn.access": {"handlers": [], "level": "WARNING", "propagate": False},
            # SQL 日志量极大且可能带参数值（等于把数据写进日志），
            # 默认压到 WARNING，开发时用 SQL_ECHO=true 单独打开。
            "sqlalchemy.engine": {
                "handlers": [],
                "level": "INFO" if settings.SQL_ECHO else "WARNING",
                "propagate": True,
            },
        },
    }


def setup_logging(level: str | None = None) -> None:
    """初始化日志系统（幂等，重复调用会整体重建 handler，不会叠加）。

    Args:
        level: 日志级别；不传则用 settings.LOG_LEVEL（来自环境变量 LOG_LEVEL）。

    Note:
        必须在任何 logger 打日志之前调用（main.py 里放在模块顶部）。
        否则最早的几条日志会走 logging 的 lastResort 处理器：只输出到 stderr、
        没有时间、没有级别 —— 恰恰是最需要格式的那几条（启动失败）。

        用 dictConfig 而不是 basicConfig 的直接后果：它会**替换** root 的
        handler 列表。uvicorn 在加载 app 之前已经配过一遍日志，我们后覆盖，
        所以最终格式由这里说了算。这也是为什么格式能统一。
    """
    logging.config.dictConfig(build_logging_config(level))
    logger.debug("日志系统已初始化，级别 %s，格式 %s", (level or settings.LOG_LEVEL).upper(),
                 "json" if settings.LOG_JSON else "console")


# ============================================================================
# 附：日志级别使用规范
# ============================================================================
# | 级别     | 什么情况用                                   | 例子
# |----------|----------------------------------------------|--------------------------------
# | DEBUG    | 只在本地排障时开，生产必须关                  | "SQL 参数: {'id': 3}"、"缓存命中 key=u:3"
# | INFO     | 正常业务节点、可审计的状态变更                | 请求完成 200/156ms、"用户 12 创建会话 34"
# | WARNING  | 预期内的失败、已自动恢复的异常、降级          | 业务异常（会话不存在）、下游超时后重试成功
# | ERROR    | 需要人介入：未捕获异常、数据不一致、写库失败  | 500 兜底（带堆栈）、第三方接口持续失败
# | CRITICAL | 进程级别的不可用：配置缺失、DB 连不上        | 启动时 SECRET_KEY 未注入、迁移版本不匹配
#
# 三条硬规矩：
# 1. 不要用 ERROR 记"用户输入错了" —— 那是 WARNING。ERROR 多了就没人看了，
#    告警阈值也会失效（告警疲劳是真实存在的运维事故来源）。
# 2. 不要把异常吞掉只打一行 warning。要么 raise，要么 logger.exception。
# 3. 日志里不许出现密码、token、Authorization 头、完整请求体。
#    原因见 app/middleware.py 顶部注释。
