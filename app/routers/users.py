"""用户相关路由（知识点 9、12）。

``GET /users/me`` 从"返回假数据"改成真正走鉴权：凭 Bearer token 拿到
当前用户，序列化成 UserOut。

这个接口现在还多了一层价值：它是**验证整条认证链路**最短的路径 ——
带 token 能通，说明 oauth2_scheme → decode_token → 查库 → is_active
四步都对了。
"""

from fastapi import APIRouter

from app.deps import CurrentUser
from app.models import User
from app.schemas import UserOut

router = APIRouter(prefix="/users", tags=["用户"])


@router.get("/me", response_model=UserOut, summary="获取当前登录用户")
async def read_current_user(user: CurrentUser) -> User:
    """返回当前 token 对应的用户资料。

    Args:
        user: 由 get_current_user 依赖注入的 ORM 对象；token 无效、
            用户不存在或已被禁用时，依赖内部已经抛了 401，走不到这里。

    Returns:
        User: 直接返回 ORM 对象即可 —— response_model=UserOut 会按字段白名单
        序列化，hashed_password 这类字段不会被带出去。
        换句话说，**安全边界在响应模型上，不在路由函数里**：即使有人在这里
        `return user` 时手滑多加了字段，也不会泄露。
    """
    return user
