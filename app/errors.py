"""统一错误格式（知识点 13）。

===============================================================================
契约：**所有**非 2xx 响应体都长这样，没有例外
===============================================================================

    {"error": {"code": "SOME_CODE", "message": "给人看的一句话", "detail": ...}}

三个字段的分工，前端按这个写解析就不会被各处不同的错误形状拖死：

- code    ：**机器**读的稳定标识。前端靠它做分支（弹什么文案、要不要跳登录页），
            所以它一旦发布就不能随便改，也不允许带变量（不能是 "USER_42_NOT_FOUND"）。
- message ：**人**读的提示。可以直接 toast 出去，但它会随文案优化而变，
            前端**不要**用 message 做判断。
- detail  ：结构化补充。校验错误是 ``[{"field","message"}]``，
            500 是 ``{"trace_id"}``，其余情况通常是 null。

为什么必须统一（而不是"HTTPException 用 detail、校验用数组、500 用纯文本"）：
前端要写三套解析，每加一个接口就要确认它属于哪一套，漏一个就是线上白屏。
统一之后前端只剩一个 ``if (!res.ok)`` 分支，加接口不用改代码。

===============================================================================
四个处理器覆盖服务端的"所有"出口
===============================================================================

1. AppError                 —— 我们自己 raise 的业务异常，带业务 code
2. RequestValidationError   —— FastAPI 参数/请求体校验失败（422）
3. StarletteHTTPException   —— HTTPException（含 FastAPI 的）、路由 404、405
4. Exception                —— 兜底，任何没被上面接住的异常

注意第 3 条的覆盖面比看起来大：**访问不存在的路由**不会进入我们的代码，
它是 Starlette 路由器抛的 ``HTTPException(404, "Not Found")``。
少了这个处理器，前端又会拿到裸的 {"detail": "Not Found"}。
"""

import uuid
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.logging_conf import logger


# ============================================================================
# 1. 异常类型
# ============================================================================
class AppError(Exception):
    """业务异常基类：抛出它代表"可预期的失败"，不是 bug。

    与 HTTPException 的分工：

    - 业务代码只 raise AppError 的子类，不直接拼 HTTPException，
      这样每个 code 都有明确的归属（谁抛的、前端该认哪个码）；
    - 路由层不需要 try/except，异常会一路冒到处理器，业务函数可以放心提前 return。

    属性：
        code: 机器可读的稳定错误码，大写下划线风格，如 CONVERSATION_NOT_FOUND。
        message: 人可读的提示，会原样出现在响应里。**不要**把内部细节写进来
            （SQL 报错、文件路径、下游 URL 都属于内部细节）。
        status_code: HTTP 状态码，默认 400。
        detail: 附加结构，默认 None；需要给前端更多上下文时再传。
        headers: 额外响应头（如 401 的 WWW-Authenticate、429 的 Retry-After）。
            统一格式**不能**把它们吞掉，否则客户端的自动重试/重新认证会失效。
    """

    def __init__(
        self,
        message: str = "请求处理失败",
        *,
        code: str = "APP_ERROR",
        status_code: int = 400,
        detail: Any = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        """初始化业务异常。

        Args:
            message: 给人看的提示文案。
            code: 机器可读的错误码。
            status_code: HTTP 状态码。
            detail: 结构化附加信息。
            headers: 需要透传给客户端的响应头。
        """
        self.message = message
        self.code = code
        self.status_code = status_code
        self.detail = detail
        self.headers = headers
        # 调 super().__init__(message)，异常被 ASGI 层意外打印时才有可读文本；
        # 但它只是 __str__ 的内容，**绝不**直接进响应体
        super().__init__(message)

    def __repr__(self) -> str:
        """调试用表示，便于在日志里一眼看出 code 与状态码。

        Returns:
            str: 形如 AppError(code=..., status_code=..., message=...)。
        """
        return (
            f"{type(self).__name__}(code={self.code!r}, "
            f"status_code={self.status_code}, message={self.message!r})"
        )


class NotFoundError(AppError):
    """资源不存在（404）。

    业务里应当把 code 说清楚，别让前端只拿到一个笼统的 NOT_FOUND：

        raise NotFoundError(code="CONVERSATION_NOT_FOUND", message="会话不存在")

    Args:
        message: 给人看的提示。
        code: 业务错误码，默认 NOT_FOUND。
        detail: 结构化附加信息。
        headers: 额外响应头。
    """

    def __init__(
        self,
        message: str = "请求的资源不存在",
        *,
        code: str = "NOT_FOUND",
        detail: Any = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        """构造 404 业务异常，见类文档。"""
        super().__init__(
            message, code=code, status_code=404, detail=detail, headers=headers
        )


class ForbiddenError(AppError):
    """已认证但无权访问（403）。

    和 401 的边界要守住：401 是"没登录/凭证失效"，403 是"登录了，但不许"。
    前端对前者跳登录页，对后者只提示，混用会导致用户被莫名其妙踢出去。

    Args:
        message: 给人看的提示。
        code: 业务错误码，默认 FORBIDDEN。
        detail: 结构化附加信息。
        headers: 额外响应头。
    """

    def __init__(
        self,
        message: str = "没有权限执行该操作",
        *,
        code: str = "FORBIDDEN",
        detail: Any = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        """构造 403 业务异常，见类文档。"""
        super().__init__(
            message, code=code, status_code=403, detail=detail, headers=headers
        )


class ConflictError(AppError):
    """资源状态冲突（409），典型场景是唯一键重复、乐观锁版本不匹配。

    Args:
        message: 给人看的提示。
        code: 业务错误码，默认 CONFLICT。
        detail: 结构化附加信息。
        headers: 额外响应头。
    """

    def __init__(
        self,
        message: str = "资源状态冲突，请刷新后重试",
        *,
        code: str = "CONFLICT",
        detail: Any = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        """构造 409 业务异常，见类文档。"""
        super().__init__(
            message, code=code, status_code=409, detail=detail, headers=headers
        )


# ============================================================================
# 2. 响应外壳与工具
# ============================================================================
def _envelope(code: str, message: str, detail: Any = None) -> dict[str, Any]:
    """拼统一响应体，保证四个处理器不会各拼一套（少一个 key 前端就崩）。

    Args:
        code: 机器可读错误码。
        message: 人可读提示。
        detail: 结构化附加信息，默认 None。

    Returns:
        dict[str, Any]: {"error": {"code": ..., "message": ..., "detail": ...}}。
    """
    return {"error": {"code": code, "message": message, "detail": detail}}


def _ensure_trace_id(request: Request) -> str:
    """取本次请求的 trace_id，没有就现生成并写回 request.state。

    trace_id 由知识点 14 的中间件在入口写入。这里兜底的原因有两个：
    中间件可能还没装；或者异常发生在中间件之前（ASGI 层直接报错）。
    取不到就生成 —— 客户端至少能拿着一个 id 找运维，
    否则"服务内部错误"这五个字对排障毫无帮助。

    Args:
        request: 当前请求。

    Returns:
        str: 32 位十六进制 trace_id。
    """
    # getattr 而不是 request.state.trace_id：Starlette 的 State 在属性不存在时
    # 抛 AttributeError，直接访问会让兜底处理器自己再崩一次（500 变 500 空响应）
    trace_id = getattr(request.state, "trace_id", None)
    if not trace_id:
        trace_id = uuid.uuid4().hex
        # 写回去，让同一请求里后续的日志与响应拿到同一个 id
        request.state.trace_id = trace_id
    return str(trace_id)


def _http_phrase(status_code: int) -> str:
    """把状态码换成标准英文短语（如 404 -> Not Found）。

    Args:
        status_code: HTTP 状态码。

    Returns:
        str: 标准短语；非标准状态码返回空串。
    """
    try:
        return HTTPStatus(status_code).phrase
    except ValueError:
        # 499/599 这类非标准码在 HTTPStatus 里不存在
        return ""


# 框架自己生成的英文 detail。它们对我们没有信息量，但会直接变成用户看到的
# message —— 中文产品里冒出一句 "Not authenticated" 是很掉价的。
# 命中这个集合就换成 _DEFAULT_MESSAGES 里的中文。
_BOILERPLATE_DETAILS: frozenset[str] = frozenset(
    {
        "Not authenticated",  # OAuth2PasswordBearer(auto_error=True)
        "Not Found",  # 路由不匹配
        "Method Not Allowed",
        "Forbidden",
        "Unauthorized",
        "Bad Request",
        "Internal Server Error",
    }
)


# 状态码 → 默认中文提示。只在"我们没有给 message"时兜底
# （典型场景：Starlette 路由器自己抛的 404，detail 是英文 "Not Found"）。
_DEFAULT_MESSAGES: dict[int, str] = {
    400: "请求参数有误",
    401: "未认证或登录状态已失效，请重新登录",
    403: "没有权限执行该操作",
    404: "请求的资源不存在",
    405: "该接口不支持此请求方法",
    409: "资源状态冲突，请刷新后重试",
    413: "请求体过大",
    415: "不支持的 Content-Type",
    422: "请求参数校验失败",
    429: "请求过于频繁，请稍后重试",
    500: "服务内部错误，请稍后重试",
    502: "上游服务异常",
    503: "服务暂时不可用，请稍后重试",
    504: "上游服务超时",
}

# 状态码 → 错误码。前端靠 code 做分支，所以必须稳定、且与 message 解耦
# （改文案不改 code，前端的判断才不会失效）。
_STATUS_CODES: dict[int, str] = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    413: "PAYLOAD_TOO_LARGE",
    415: "UNSUPPORTED_MEDIA_TYPE",
    422: "VALIDATION_ERROR",
    429: "TOO_MANY_REQUESTS",
    500: "INTERNAL_ERROR",
    502: "BAD_GATEWAY",
    503: "SERVICE_UNAVAILABLE",
    504: "GATEWAY_TIMEOUT",
}


# ============================================================================
# 3. 校验错误的"前端友好"翻译
# ============================================================================
# Pydantic 的 detail 长这样（嵌套 loc + 英文 msg）：
#   [{"type": "missing", "loc": ["body", "message"], "msg": "Field required", ...}]
# 直接丢给前端有三个问题：英文、loc 是数组要自己 join、ctx 里可能塞着
# ValueError 对象（JSON 序列化会失败，把 422 变成 500）。
# 这里翻译成 [{"field": "body.message", "message": "必填"}]：
#   - field 是点分路径，前端用 lodash.get(form, field) 就能定位到出错的输入框；
#   - message 是可以直接展示的中文。
_VALIDATION_MESSAGES: dict[str, str] = {
    "missing": "必填",
    "extra_forbidden": "不支持的字段",
    "string_type": "必须是字符串",
    "string_too_short": "长度不能少于 {min_length} 个字符",
    "string_too_long": "长度不能超过 {max_length} 个字符",
    "string_pattern_mismatch": "格式不合法",
    "int_type": "必须是整数",
    "int_parsing": "必须是整数",
    "float_type": "必须是数字",
    "float_parsing": "必须是数字",
    "bool_type": "必须是布尔值",
    "bool_parsing": "必须是布尔值",
    "greater_than": "必须大于 {gt}",
    "greater_than_equal": "不能小于 {ge}",
    "less_than": "必须小于 {lt}",
    "less_than_equal": "不能大于 {le}",
    "list_type": "必须是数组",
    "dict_type": "必须是对象",
    "enum": "取值不在允许范围内",
    "literal_error": "取值不在允许范围内",
    "json_invalid": "请求体不是合法的 JSON",
    "datetime_parsing": "时间格式不合法",
    "date_parsing": "日期格式不合法",
    "url_parsing": "URL 格式不合法",
}


def _format_field(loc: tuple[Any, ...] | list[Any]) -> str:
    """把 Pydantic 的 loc 数组拼成点分路径。

    Args:
        loc: 形如 ("body", "options", "temperature") 或 ("body", 0, "message")。

    Returns:
        str: 形如 "body.options.temperature"；loc 为空时返回 "body"。

    Note:
        数组下标也拼接进去（"body.messages.0.content"），
        这样前端递归定位列表项时同样有一条完整路径可用。
    """
    if not loc:
        # 极端情况：整个 body 缺失时 loc 可能是空元组
        return "body"
    return ".".join(str(part) for part in loc)


def _format_message(error: dict[str, Any]) -> str:
    """把单条 Pydantic 错误翻译成中文提示。

    Args:
        error: exc.errors() 里的一项，含 type / msg / ctx。

    Returns:
        str: 可直接展示的文案；没有对应翻译时回退到 Pydantic 原文。
    """
    error_type = error.get("type", "")
    template = _VALIDATION_MESSAGES.get(error_type)

    if template:
        ctx = error.get("ctx") or {}
        try:
            return template.format(**ctx)
        except (KeyError, IndexError, ValueError):
            # ctx 里缺占位符（Pydantic 版本差异）时不能让处理器自己崩
            return template

    raw = str(error.get("msg", "参数不合法"))

    # 自定义 validator / EmailStr 走 value_error，Pydantic 会加
    # "Value error, " 前缀 —— 对用户没有意义，去掉
    if raw.startswith("Value error, "):
        raw = raw[len("Value error, ") :]

    # EmailStr 的原文是 "value is not a valid email address: ..."，
    # 太技术了，换成一句人话
    if "not a valid email address" in raw:
        return "邮箱格式不正确"

    return raw


def format_validation_errors(exc: RequestValidationError) -> list[dict[str, str]]:
    """把 RequestValidationError 转成 [{"field", "message"}]。

    Args:
        exc: FastAPI 抛出的校验异常。

    Returns:
        list[dict[str, str]]: 每条含 field（点分路径）与 message（中文提示），
        重复项按 (field, message) 去重并保持原顺序。
    """
    formatted: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for error in exc.errors():
        error_type = error.get("type", "")
        loc = error.get("loc", ())
        # json_invalid（body 不是合法 JSON）的 loc 是 ("body", 出错的字符位置)，
        # 那个整数是**字符偏移量**而不是数组下标，拼出来会变成 "body.1" 这种
        # 前端无法定位的假路径 —— 这种情况统一归到 "body"
        item = {
            "field": "body" if error_type == "json_invalid" else _format_field(loc),
            "message": _format_message(error),
        }
        key = (item["field"], item["message"])
        # 联合类型（str | None 等）会对同一字段报多条几乎相同的错，
        # 不去重的话前端会同时弹好几个 toast
        if key in seen:
            continue
        seen.add(key)
        formatted.append(item)

    if not formatted:
        # 理论上 errors() 不会为空，但契约要求 detail 永远是数组，
        # 这里兜一条，避免前端拿到 null 后 .map 直接崩
        formatted.append({"field": "body", "message": "请求参数不合法"})

    return formatted


# ============================================================================
# 4. 四个处理器
# ============================================================================
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    """处理业务异常 AppError（预期内的失败，不记堆栈）。

    Args:
        request: 当前请求，只取 method/path 做日志补全。
        exc: 业务异常实例。

    Returns:
        JSONResponse: 统一结构，状态码与 headers 取自异常本身。

    Note:
        业务异常是"设计好的失败"，logger.warning 记一行就够。
        打完整堆栈只会把真正的 bug 淹掉 —— 一个用户狂点不存在的会话，
        就能刷出上百条无用的堆栈。
    """
    logger.warning(
        "业务异常 | %s %s | %s | %s",
        request.method,
        request.url.path,
        exc.code,
        exc.message,
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=_envelope(exc.code, exc.message, exc.detail),
        # headers 必须透传：401 的 WWW-Authenticate、429 的 Retry-After 都靠它，
        # 统一格式不能成为丢头的理由
        headers=exc.headers,
    )


async def validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """处理参数/请求体校验失败（422）。

    Args:
        request: 当前请求。
        exc: FastAPI 的校验异常，errors() 是 Pydantic 的原始错误列表。

    Returns:
        JSONResponse: 422 + detail 为 [{"field", "message"}] 数组。

    Note:
        刻意**不**把 exc.errors() 原样返回：里面有 loc 数组、英文 msg、
        ctx 里的 ValueError 对象，既不好用也不好序列化。
    """
    detail = format_validation_errors(exc)
    logger.info(
        "参数校验失败 | %s %s | %s",
        request.method,
        request.url.path,
        [item["field"] for item in detail],
    )
    return JSONResponse(
        status_code=422,
        content=_envelope("VALIDATION_ERROR", "请求参数校验失败", detail),
    )


async def http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    """处理 HTTPException（含 FastAPI 的 HTTPException 与路由 404/405）。

    Args:
        request: 当前请求。
        exc: Starlette 的 HTTPException，detail 可能是字符串、字典或列表。

    Returns:
        JSONResponse: 统一结构，headers 原样保留。

    Note:
        这是覆盖面最广的处理器：**访问不存在的路由**不会进入任何业务代码，
        而是路由器抛 ``HTTPException(404, "Not Found")``。
        少了这一层，前端仍会拿到裸的 {"detail": "Not Found"}。
    """
    detail = exc.detail
    default_message = _DEFAULT_MESSAGES.get(exc.status_code, "请求处理失败")

    # 三种情况：
    # 1. detail 是业务自己写的字符串（"用户名或密码错误"）→ 直接当 message
    # 2. detail 是 Starlette 的英文短语（"Not Found"）→ 换成中文
    # 3. detail 不是字符串（dict / list）→ 放进 detail 字段，message 用默认值
    if isinstance(detail, str) and detail.strip():
        if detail == _http_phrase(exc.status_code) or detail in _BOILERPLATE_DETAILS:
            message = default_message
            detail_payload = None
        else:
            message = detail
            detail_payload = None
    else:
        message = default_message
        detail_payload = detail

    return JSONResponse(
        status_code=exc.status_code,
        content=_envelope(
            _STATUS_CODES.get(exc.status_code, "HTTP_ERROR"), message, detail_payload
        ),
        # 405 的 Allow、401 的 WWW-Authenticate 都在这里，
        # 丢了会破坏客户端的自动重试/重新认证行为
        headers=getattr(exc, "headers", None),
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """兜底处理器：任何没被前面接住的异常（即真正的 bug）。

    Args:
        request: 当前请求。
        exc: 未捕获的异常。

    Returns:
        JSONResponse: 500 + INTERNAL_ERROR，detail 里只有 trace_id。

    Note:
        **绝不**把 str(exc) 或堆栈返回给客户端：

        - 异常文本经常带内部细节（SQL 语句、表名、文件绝对路径、
          下游 host、依赖库版本），是攻击者做侦察最省力的入口；
        - 堆栈暴露框架与版本，等于把可用的 CVE 清单递过去；
        - 用户看不懂，也没有任何可操作性。

        客户端只需要 trace_id：报障时给到服务端，日志里能按它捞出完整堆栈。
        堆栈只进日志（logger.exception），这是唯一正确的去处。

        另一个必须知道的细节：500 是由 Starlette 最外层的 ServerErrorMiddleware
        生成的，它在发完响应后**仍会把异常重新抛出**给 ASGI 服务器，
        所以 uvicorn 的控制台上还会看到一次 "Exception in ASGI application"。
        这是正常现象，不是你的处理器没生效。
    """
    trace_id = _ensure_trace_id(request)

    # logger.exception = ERROR 级别 + 自动附带当前异常的完整堆栈。
    # 带上 method/path/trace_id，是为了让"一条 500 日志"自带定位线索。
    # 注意：不要在这里打请求体或请求头 —— 里面可能有密码和 token。
    logger.exception(
        "未捕获异常 | trace_id=%s | %s %s",
        trace_id,
        request.method,
        request.url.path,
    )

    return JSONResponse(
        status_code=500,
        content=_envelope(
            "INTERNAL_ERROR",
            "服务内部错误，请稍后重试",
            # trace_id 放在 detail 里而不是顶层，是为了让外壳形状保持恒定：
            # 前端永远只解析 error.code / error.message / error.detail 三个键
            {"trace_id": trace_id},
        ),
    )


# ============================================================================
# 5. 一次性注册
# ============================================================================
def register_exception_handlers(app: FastAPI) -> None:
    """把四个处理器挂到 app 上（main.py 里调用一次即可）。

    Args:
        app: FastAPI 实例。

    Note:
        注册顺序不影响结果（Starlette 按异常类型精确匹配 + 沿 MRO 查找），
        但 **Exception 必须注册**：不注册它，Starlette 会用自己的默认处理器，
        返回纯文本 "Internal Server Error"，前端 res.json() 直接抛错。
    """
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
