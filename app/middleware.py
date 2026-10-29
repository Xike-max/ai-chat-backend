"""请求追踪中间件：给每个请求分配 trace_id，并记录一条访问日志。

**为什么用纯 ASGI 中间件，而不是 BaseHTTPMiddleware**
------------------------------------------------------------------
这是本文件唯一一个"看起来绕远路"的决定，必须先说清楚。

`BaseHTTPMiddleware` 会把下游调用放进**另一个任务**（`call_next` 内部
`anyio.create_task_group`），而 `contextvars` 的副本是在任务创建时快照的：
在 `dispatch()` 里 `set_trace_id()`，路由函数所在的那个任务看不到它，
日志里的 trace_id 永远是 "-"。表现就是"中间件明明写了，日志就是不显示"。

纯 ASGI 中间件全程在同一个任务里跑，`set_trace_id` → 路由 → 异常处理器
→ `finally` 都是同一个上下文，trace_id 自然贯穿到底。

额外好处：能直接改写 `http.response.start` 消息，在**响应头**上加
`X-Trace-Id`，客户端报障时把它贴过来即可定位。

**明确禁止记录的内容（以及为什么）**
------------------------------------------------------------------
1. **请求体原文**：注册/登录接口的 body 里就是明文密码；对话接口的 body
   是用户的聊天内容（隐私）。日志会被采集、转发、长期存储、给第三方看 ——
   等于把密码和隐私抄送了一份给所有能读日志的人。
2. **Authorization / Cookie 头**：JWT 就是"bearer 凭证"，拿到即等于登录态。
   日志系统的读权限通常比数据库宽得多（运维、外包、日志 SaaS 都能看），
   所以 token 一旦进日志，等于身份凭证泄露。
3. **password / token / secret 等字段**：即使只打"部分参数"也一样危险，
   字段名匹配不上就漏了（password_confirmation、newPwd、otp…）。

结论：中间件只记录**方法、路径、状态码、耗时**。连 query string 都不记 ——
`?access_token=xxx` 这种写法太常见了。真需要排查参数时，用 trace_id 关联
业务日志里**脱敏后**的关键字段，而不是把原始请求抄一遍。
"""

from __future__ import annotations

import string
import time
import uuid

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.logging_conf import (
    PROCESS_TIME_HEADER,
    TRACE_ID_HEADER,
    access_logger,
    reset_trace_id,
    set_trace_id,
)

#: 允许出现在 trace_id 里的字符：字母、数字、`-`、`_`、`.`。
#: 为什么要校验而不是"客户端给什么就用什么"——日志注入：
#: 传进来的值会被原样拼进日志行，如果允许 `\n`，攻击者可以伪造出一整行
#: 假的 `ERROR ... 用户已提权`；如果允许 ANSI 转义码，还能篡改终端显示
#: （把日志刷成别的颜色/清屏，掩盖真实记录）。所以只放行"安全字符集"。
_SAFE_TRACE_ID_CHARS = frozenset(string.ascii_letters + string.digits + "-_.")

#: 长度上限。UUID 是 36 字符、hex 是 32 字符，64 已经足够宽松；
#: 不限长的话，一个 1MB 的 header 就能把每行日志撑爆。
_MAX_TRACE_ID_LENGTH = 64


def _clean_trace_id(raw: str | None) -> str | None:
    """校验并规整客户端传来的 trace_id。

    Args:
        raw: 请求头 X-Trace-Id 的原始值，可能为 None。

    Returns:
        str | None: 合法则返回去空格后的值，不合法返回 None（调用方会改用新生成的 id）。

    Note:
        校验失败**不**报 400：trace_id 是辅助排障的，不是业务参数，
        为了一个追踪头把正常请求打回去，属于本末倒置。
    """
    if not raw:
        return None
    candidate = raw.strip()
    if not candidate or len(candidate) > _MAX_TRACE_ID_LENGTH:
        return None
    if not set(candidate) <= _SAFE_TRACE_ID_CHARS:
        return None
    return candidate


class TraceMiddleware:
    """给每个 HTTP 请求分配 trace_id，记录访问日志，并把 id 回写到响应头。

    使用方法（注意顺序）：

        app.add_middleware(TraceMiddleware)   # 后加的在**外层**

    放在最外层是为了让 CORS 预检（OPTIONS）也被记录，并且保证
    `X-Trace-Id` 会加到包括预检在内的所有响应上。
    """

    def __init__(self, app: ASGIApp) -> None:
        """保存下游 ASGI 应用。

        Args:
            app: 被包裹的 ASGI 应用（Starlette 会把下一个中间件或路由传进来）。
        """
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """处理一次 ASGI 调用。

        Args:
            scope: ASGI 连接描述（含 method / path / headers）。
            receive: 接收客户端消息的通道。
            send: 发送响应消息的通道。

        Note:
            非 http 的 scope（lifespan 启动/关闭、websocket）直接放行：
            lifespan 没有请求头也没有响应头，硬塞 trace_id 只会让启动日志
            变得莫名其妙；websocket 需要另一套长连接处理，不在本节范围。
        """
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        # 优先用客户端传的 id：这是跨服务串联的关键 —— 网关或上游服务
        # 已经生成了一个 id，下游如果重新生成，一次调用就会在日志里断成两截。
        trace_id = _clean_trace_id(headers.get(TRACE_ID_HEADER)) or uuid.uuid4().hex

        # 写两处，各有各的消费者：
        # 1. scope["state"] -> request.state.trace_id，给业务代码和 errors.py 的
        #    异常处理器读（它们拿不到 LogRecord）；
        # 2. contextvar -> 给日志 Filter 读，让所有 logger 自动带上。
        #    setdefault 是因为 Starlette 的 Request.state 也读写这个 dict，
        #    我们若直接覆盖成新 dict，会把上游已经放进去的东西清掉。
        scope.setdefault("state", {})["trace_id"] = trace_id
        scope["state"]["request_start"] = time.perf_counter()
        token = set_trace_id(trace_id)

        method: str = scope.get("method", "-")
        # 只取 path，不含 query string（见文件顶部的说明）。
        # scope["path"] 已经是解码后的路径，不会带 %XX。
        path: str = scope.get("path", "-")

        start = time.perf_counter()
        # 默认 500：如果下游抛异常、根本没发出 http.response.start，
        # finally 里的访问日志就按 500 记 —— 这个默认值让"异常请求"
        # 在日志里也有一行完整的访问记录，而不是凭空消失。
        status_code = 500

        async def send_wrapper(message: Message) -> None:
            """在响应开始发送时补上两个头，其余消息原样透传。

            Args:
                message: ASGI 响应消息。

            Note:
                只拦 `http.response.start`：`http.response.body` 可能有几十条
                （流式输出），在那里加头是无效的，而且会拖慢首字节时间。
            """
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                response_headers = MutableHeaders(scope=message)
                response_headers[TRACE_ID_HEADER] = trace_id
                response_headers[PROCESS_TIME_HEADER] = (
                    f"{(time.perf_counter() - start) * 1000:.1f}ms"
                )
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            # 只记录状态码，**不吞异常**：把 500 变成统一 JSON 是
            # errors.py 兜底处理器的职责（堆栈也只在那里打一次，
            # 这里再打一遍就是同一份堆栈出现两次，反而干扰定位）。
            # 这里保留 except 的意义是：让"异常"和"正常返回 5xx"在日志里
            # 可区分——前者没有 response.start，状态码只能由这里兜底。
            status_code = 500
            raise
        finally:
            # finally 里只引用标量（status_code / start / method / path），
            # **绝不引用 response 对象**：异常路径下 response 根本没生成，
            # 引用它必然再抛一次 AttributeError，把真正的异常盖掉。
            elapsed_ms = (time.perf_counter() - start) * 1000
            access_logger.info(
                "%s %s -> %s | %.1fms",
                method,
                path,
                status_code,
                elapsed_ms,
                # 慢请求单独提级，方便直接 grep "慢请求" 而不必自己算阈值
                extra={"http_status": status_code, "duration_ms": round(elapsed_ms, 1)},
                # 注意：status >= 500 时不要在这里 exc_info —— 堆栈由兜底
                # 处理器打，这里只是一行"账本"
                stacklevel=1,
            )
            if elapsed_ms > 1000:
                access_logger.warning(
                    "慢请求 | %s %s -> %s | %.1fms", method, path, status_code, elapsed_ms
                )
            # 必须 reset：否则这个 trace_id 会残留在当前上下文中，
            # 被后续复用同一上下文的日志（如后台任务）错误继承。
            reset_trace_id(token)


__all__ = ["TraceMiddleware"]
