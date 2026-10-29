"""用户相关路由（知识点 9、12）。

``GET /users/me`` 从"返回假数据"改成真正走鉴权：凭 Bearer token 拿到
当前用户，序列化成 UserOut。

这个接口现在还多了一层价值：它是**验证整条认证链路**最短的路径 ——
带 token 能通，说明 oauth2_scheme → decode_token → 查库 → is_active
四步都对了。
"""

from fastapi import APIRouter

from app.deps import CurrentUser
from app.schemas import UserOut

router = APIRouter(prefix="/users", tags=["用户"])


@router.get("/me", response_model=UserOut, summary="获取当前登录用户")
async def read_current_user(user: CurrentUser) -> UserOut:
    """返回当前 token 对应的用户资料。

    收窄返回值类型后，这个路由**不再 import ORM 模型**：它只知道"进一个
    已鉴权的用户，出一个对外的用户视图"。分层上这比 `-> User` 更干净 ——
    路由层不持有 ORM 类，也就没法顺手 `user.hashed_password` 或
    `db.query(User)`。

    Args:
        user: 由 get_current_user 依赖注入的 ORM 对象；token 无效、
            用户不存在或已被禁用时，依赖内部已经抛了 401，走不到这里。

    Returns:
        UserOut: 按字段白名单序列化后的用户视图（不含 hashed_password）。
        安全边界在响应模型上，不在路由函数里。
    """
    return UserOut.model_validate(user)
