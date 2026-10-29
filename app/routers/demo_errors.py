"""故意出错的演示路由（知识点 13）。

用途只有一个：让统一错误格式能被**亲手**触发一遍，而不是只在文档里读一遍。
生产的 500 往往要等线上出事才能看到长什么样，这里把五种情况都做成了固定入口。

⚠️ 只在非生产环境挂载（见 app/main.py）：一个能稳定抛 ZeroDivisionError 的
接口留在生产上，等于给攻击者一个白送的 500 发生器，还会污染错误率监控。

五种错误类型与对应入口：

    1. AppError 子类（业务 404）  →  GET /api/v1/conversations/999999
    2. 参数校验失败（422）        →  POST /api/v1/auth/register  (body 少字段)
    3. 路由不存在（404）          →  GET /api/v1/nope
    4. 未捕获异常（500）          →  GET /api/v1/demo/errors/boom
    5. 401 + WWW-Authenticate     →  GET /api/v1/users/me         (不带 token)

本文件只演示 3、4 里没有现成入口的那几个。
"""

from fastapi import APIRouter, Depends

from app.deps import get_current_user
from app.errors import ConflictError, ForbiddenError, NotFoundError

# 演示接口也要鉴权：它虽然不读业务数据，但能主动制造 500/错误日志，
# 匿名可打等于给外部留了一个"免费的错误率制造器"。开发时先登录拿 token 再打。
router = APIRouter(
    prefix="/demo/errors",
    tags=["错误格式演示"],
    dependencies=[Depends(get_current_user)],
)


@router.get("/boom", summary="故意抛未捕获异常，演示 500 兜底")
async def boom() -> int:
    """除以零，触发兜底处理器。

    这里**不**写 try/except：真实项目里的 bug 就是没人接住的异常，
    如果这里自己 catch 了，演示的就不是"兜底"而是"自己处理"。

    Returns:
        int: 永远不会返回。

    Raises:
        ZeroDivisionError: 一定会抛，用于验证 500 响应只暴露 trace_id。
    """
    divisor = 0
    # 用变量而不是字面量 1 / 0：某些 linter 会把字面量除零当成语法级错误直接报
    return 1 // divisor


@router.get("/forbidden", summary="演示 403 业务异常")
async def forbidden() -> None:
    """抛 ForbiddenError，演示"已登录但无权"的响应。

    Raises:
        ForbiddenError: 403 + code=CONVERSATION_FORBIDDEN。
    """
    raise ForbiddenError(
        code="CONVERSATION_FORBIDDEN",
        message="无权访问该会话",
        # detail 可以用结构化数据告诉前端"缺什么权限"，
        # 但别放内部 id 或角色枚举之外的敏感信息
        detail={"required_scope": "conversation:read"},
    )


@router.get("/conflict", summary="演示 409 业务异常")
async def conflict() -> None:
    """抛 ConflictError，演示唯一键冲突一类的失败。

    Raises:
        ConflictError: 409 + code=USERNAME_TAKEN。
    """
    raise ConflictError(code="USERNAME_TAKEN", message="用户名已被占用")


@router.get("/not-found", summary="演示 404 业务异常")
async def not_found() -> None:
    """抛 NotFoundError，和 /conversations/{id} 的写法一致。

    Raises:
        NotFoundError: 404 + code=CONVERSATION_NOT_FOUND。
    """
    raise NotFoundError(code="CONVERSATION_NOT_FOUND", message="会话不存在")
