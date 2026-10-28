"""依赖用法示例（知识点 7、11、12）。

本文件只为演示依赖声明方式，不含业务逻辑；把这段复制到任意新路由里即可直接跑。
和上一版相比，``get_current_user_id``（读 X-User-Id 头）已经换成
``CurrentUser``（解析 Bearer token 并回查数据库）—— 前者是过渡期的占位实现，
不能区分"谁在调用"，任何人改一下请求头就能变成别人。
"""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.deps import CurrentUser, Pagination, verify_api_key

router = APIRouter(prefix="/demo", tags=["依赖示例"])


@router.get("/echo", dependencies=[Depends(verify_api_key)], summary="依赖用法演示")
async def demo_echo(
    pagination: Annotated[Pagination, Depends()],
    user: CurrentUser,
) -> dict[str, object]:
    """演示三种依赖写法：装饰器级 API Key + 类依赖分页 + 函数依赖取当前用户。

    Args:
        pagination: 类依赖，FastAPI 自动实例化并解析 skip / limit。
        user: 函数依赖（含子依赖链），内部已校验 Bearer token 并查过库。

    Returns:
        dict[str, object]: 回显分页派生值与当前用户，用于验证依赖确实注入成功。
    """
    return {
        "user_id": user.id,
        "username": user.username,
        "skip": pagination.skip,
        "limit": pagination.limit,
        "offset": pagination.offset,
        "page": pagination.page,
    }
