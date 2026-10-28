"""通用依赖零件（知识点 7、11、12）。

这里只放"可复用的依赖"，不放业务逻辑。四个依赖各解决一件事：

- get_db            ：每个请求一个数据库会话（实现放在 database.py，此处转发）
- Pagination        ：把 skip / limit 的声明、校验、派生值收在一处
- verify_api_key    ：服务间调用的静态凭证校验（装饰器级用法）
- get_current_user  ：从 Bearer token 解析出**数据库里的当前用户**

注意：本文件**不能**加 `from __future__ import annotations`。
它会把 __init__ 里的 Annotated[...] 变成字符串，FastAPI 解析依赖签名时会抛
PydanticUserError（ForwardRef 未解析），表现为一访问接口就 500。
"""

import secrets
from collections.abc import Sequence
from typing import Annotated, TypeVar

from fastapi import Depends, Header, HTTPException, Query, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.auth import decode_token
from app.config import settings

# 会话依赖的唯一实现在 app/database.py，这里只做转发：
# 两份 try/finally 迟早会分叉（一边加了 rollback，另一边忘了 close），
# 所以宁可少写一份，也不要让“哪个才是真的”变成后来的疑问。
from app.database import get_db  # noqa: F401
from app.models import User

T = TypeVar("T")


# ============================================================================
# 1. 数据库会话
# ============================================================================
# get_db 见上方 import：定义在 app/database.py，与 engine / SessionLocal 同文件，
# 路由里仍然可以 `from app.deps import get_db`，对外接口不变。


# ============================================================================
# 2. 分页参数（类依赖）
# ============================================================================
class Pagination:
    """分页依赖：集中处理 skip / limit 的校验与派生值。

    用法（类依赖不传参数，FastAPI 会自己实例化）::

        @router.get("")
        async def list_x(pagination: Annotated[Pagination, Depends()]):
            ...

    参数非法（skip < 0、limit < 1、limit > 100）时由 FastAPI 直接返回 422，
    不会进到业务函数，因此不存在“分页参数写错导致 500”的情况。
    """

    def __init__(
        self,
        skip: Annotated[int, Query(ge=0, description="跳过的记录条数，等价于 offset")] = 0,
        limit: Annotated[int, Query(ge=1, le=100, description="每页条数，最多 100")] = 20,
    ) -> None:
        self.skip = skip
        self.limit = limit

    @property
    def offset(self) -> int:
        """SQL 查询要用的 offset，与 skip 等值（为知识点 9 预留语义）。"""
        return self.skip

    @property
    def page(self) -> int:
        """当前页码，从 1 开始，供前端展示“第 N 页”。"""
        return self.skip // self.limit + 1

    @property
    def is_first_page(self) -> bool:
        """是否第一页。"""
        return self.skip == 0

    def paginate(self, rows: Sequence[T]) -> list[T]:
        """对已筛选、未分页的内存数据做切片。

        Args:
            rows: 已应用筛选条件、但尚未分页的数据。

        Returns:
            list[T]: 当前页数据；越界时返回空列表而不是报错。
        """
        return list(rows[self.skip : self.skip + self.limit])


# ============================================================================
# 3. API Key 校验（只做副作用，不返回值）
# ============================================================================
# 401 的文案只写一份、两种情况共用。**不要**拆成
# "缺少 API Key" / "API Key 不正确" 两句：那等于给攻击者一个免费探测器，
# 可以先用空值确认接口存在，再用错误值确认"服务端确实配了 key、只是我猜错了"。
# 统一成一句，攻击者从响应里读不出任何额外信息。
_API_KEY_REJECTED = "API Key 无效或缺失，请在请求头 X-API-Key 中提供有效凭证"


def verify_api_key(
    x_api_key: Annotated[
        str | None,
        Header(
            alias="X-API-Key",
            description="服务间调用凭证，只从请求头读取（不要放 query）",
        ),
    ] = None,
) -> None:
    """校验请求头里的 X-API-Key（解决"内部接口裸奔"问题）。

    只从请求头读，**刻意不接受 query 参数**（不接受 `?api_key=xxx`）：

    - query 会原样写进 Nginx / 网关 / uvicorn 的 access log，
      凭证就此长期躺在日志文件里，能读日志的人都能冒充这台服务；
    - 会进浏览器历史、书签、以及用户复制链接时带出去；
    - 作为 Referer 随页面里的第三方资源请求发出去，等于外泄给广告/CDN 域名；
    - 反向代理的 URL 监控、APM 采样通常也会记录完整 URL。

    这也是为什么它必须是 Header：请求头默认不落盘、不进 URL、有独立的日志策略。

    这是"只做事、不产出值"的依赖，正确用法是挂在 router 或路由装饰器上：

        router = APIRouter(dependencies=[Depends(verify_api_key)])
        @router.get("/x", dependencies=[Depends(verify_api_key)])

    不要写成 `api_key = Depends(verify_api_key)` 然后在函数体里用 api_key，
    因为它永远返回 None，那样写只会让人误以为拿到了凭证。

    Args:
        x_api_key: 请求头 X-API-Key 的原始值；没传时为 None。

    Raises:
        HTTPException: 401，请求头缺失或值不匹配（两种情况文案完全一致）。
    """
    # 用 secrets.compare_digest 而不是 ==：
    # Python 的 == 对 str/bytes 是**短路比较**，发现第一个不同字节就立刻返回。
    # 于是"前 3 位对了"和"第 1 位就错了"的响应时间会有纳秒级差异，
    # 攻击者可以在同一台机器上反复请求取均值，一个字节一个字节地把 key 试出来
    # （时序侧信道 / timing attack，对固定长凭证尤其有效）。
    # compare_digest 的耗时只与长度有关、与内容无关，把这条信道堵死。
    #
    # 必须转成 bytes 再比：compare_digest 对含非 ASCII 的 str 会抛 TypeError，
    # 请求头里塞个中文就变成 500 了（等于把 401 变成"服务异常"，还暴露栈信息）。
    provided = (x_api_key or "").encode("utf-8")
    expected = settings.API_KEY.encode("utf-8")
    if not secrets.compare_digest(provided, expected):
        # 注意 detail 用的是上面那个常量，缺失与错误共用，不做区分。
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_API_KEY_REJECTED,
            # WWW-Authenticate 是 401 的规范要求：告诉客户端"该用哪种认证方案"。
            # 值 "ApiKey" 不是标准 scheme（标准是 Basic/Bearer/Digest），
            # 但这里就是自有方案，写清楚比留空更有用；漏了它，部分 HTTP 客户端
            # 和网关会认为这是个残缺的 401。
            headers={"WWW-Authenticate": "ApiKey"},
        )
    # 校验通过直接返回，不做任何 return 值。


# ----------------------------------------------------------------------------
# 【生产版 TODO】现在的 key 比对只够单机 / 内部联调，上线前应换成数据库托管
#
# 当前实现的两个硬伤：
#   1. 全局只有一个 key，无法按调用方吊销 —— 运维脚本泄露了，只能全量换 key，
#      所有服务同时中断；
#   2. key 明文躺在 .env 里，能读部署配置的人就能读 key，也无法审计"谁在什么时候用过"。
#
# 建议的表结构（不用现在实现，先写在这里对齐方向）：
#
#   CREATE TABLE api_clients (
#       id            INTEGER PRIMARY KEY,
#       name          VARCHAR(64)  NOT NULL,        -- 调用方名称，如 "ops-scripts"
#       owner         VARCHAR(120) NOT NULL,        -- 负责人，吊销时知道找谁
#       key_prefix    VARCHAR(12)  NOT NULL,        -- key 前 8~12 位明文，便于在日志/后台定位是哪把
#       key_hash      VARCHAR(256) NOT NULL,        -- 只存哈希：sha256(key) 或 argon2/bcrypt
#       scopes        VARCHAR(200) NOT NULL DEFAULT '',  -- 逗号分隔的权限，如 "usage:write"
#       enabled       BOOLEAN      NOT NULL DEFAULT 1,   -- 吊销 = 置 0，不删行（保留审计）
#       last_used_at  TIMESTAMP,                    -- 每次校验成功后更新，用于发现"僵尸 key"
#       expires_at    TIMESTAMP,                    -- 可选：临时 key 的到期时间
#       created_at    TIMESTAMP    NOT NULL,
#       UNIQUE (key_prefix)
#   );
#
# 校验流程（替换本函数内部实现，签名与用法不变）：
#   1. 取 X-API-Key，按固定前缀查 api_clients（走 UNIQUE(key_prefix) 索引，
#      避免对全表做哈希比对）；
#   2. 命中后校验 key_hash：仍是 secrets.compare_digest，别用 ==；
#   3. enabled=1 且未过期才放行；
#   4. last_used_at 异步更新（或按分钟聚合后批量写），不要每次请求都 UPDATE，
#      否则这把表变成写热点。
#
# 另外：哈希要加盐/用慢哈希（argon2/bcrypt）。key 本身是高熵随机串（32+ 字节），
# 用 sha256 已够；但如果 key 允许人工设置（低熵），必须换慢哈希，否则彩虹表可破。
# ----------------------------------------------------------------------------


# ============================================================================
# 4. 当前用户（Bearer token → 数据库里的 User）
# ============================================================================
# OAuth2PasswordBearer 做两件事：
#   1. 从 Authorization 头里按 "Bearer <token>" 解析出 token
#      （缺头或前缀不对时自动 401，且响应自带 WWW-Authenticate: Bearer）；
#   2. 给 OpenAPI 声明安全方案 —— Swagger UI 右上角因此出现 Authorize 按钮。
#
# tokenUrl 写的是**带 /api/v1 前缀的完整路径**，不是相对路径：
# Swagger UI 的 Authorize 表单会直接向这个地址 POST 表单换 token，
# 写错一个字就会在那里得到 404，而接口本身却是好的，很难往配置上想。
oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="/api/v1/auth/token",
    description="登录接口返回的 access_token，传法：Bearer <token>",
)

# 401 文案只写一份，并在"解析失败 / 用户不存在 / 已禁用"三种情况下共用。
# 任何差异化提示（"用户不存在" vs "token 无效"）都是在告诉攻击者
# "你的 token 结构是对的，只是这个 id 没人用" —— 那正是枚举用户 id 的探针。
_CREDENTIALS_REJECTED = "登录状态无效或已过期，请重新登录"


def _credentials_exception() -> HTTPException:
    """构造统一的 401 异常（认证失败的唯一出口）。

    Returns:
        HTTPException: 401，带 ``WWW-Authenticate: Bearer`` 响应头。

    Note:
        必须每次新建实例。同一个异常对象被反复 raise 时 traceback 会不断叠加，
        日志里看到的调用栈会指向第一次抛出的位置，排障时容易被带偏。
    """
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=_CREDENTIALS_REJECTED,
        # RFC 7235 要求 401 必须带 WWW-Authenticate，否则客户端不知道
        # 该用什么方案重新认证；值必须是 Bearer（不是 Token / JWT）。
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    """从 Bearer token 解析出当前用户（解决"每个接口自己解析身份"的问题）。

    执行顺序：

        1. oauth2_scheme     —— 取 Authorization 头，缺失/格式错直接 401
        2. decode_token      —— 校验签名与 exp，失败返回 None
        3. 取 sub 并转 int    —— 缺失或不是数字 → 401
        4. 查库              —— 用户不存在 → 401
        5. 判 is_active      —— 被禁用 → 401（**不是 403**）
        6. 路由函数          —— 拿到的是 ORM 对象，不是裸 id

    为什么返回 User 而不是 user_id：
        业务侧几乎总要 is_active、将来的 role/quota 等字段，返回 id 等于让每个
        接口再查一次库；而这里已经拿到了会话和对象，顺带返回零成本。
        反过来说，**权限判断必须用这里的对象**，绝不能用 token 里的 claims
        （claims 是签发时的快照，可能已经过期，见 app/auth.py 的说明）。

    为什么禁用账号是 401 而不是 403：
        403 的语义是"我认识你，但你没权限访问这个资源"，前端通常会停留在页面上
        继续请求、只是提示"无权限"；而账号被禁用属于**凭据不再有效**，
        语义上等同"未登录"，前端应当跳登录页。
        用 403 还会让被禁用的用户以为"换个页面也许能用"，实际每个接口都得单独
        处理 403 的分支 —— 统一成 401，前端只需要一个拦截器。

    Args:
        token: oauth2_scheme 解析出的原始 JWT。
        db: 当前请求的数据库会话。

    Returns:
        User: 数据库里的当前用户 ORM 对象。

    Raises:
        HTTPException: 401，token 无效/过期/sub 非法/用户不存在/账号被禁用。
    """
    payload = decode_token(token)
    if payload is None:
        # 过期、签名错、格式错都在这里被收敛成同一种结果
        raise _credentials_exception()

    subject = payload.get("sub")
    # decode 时已经 require 了 sub，但类型得自己保证：
    # sub 是 "1" 还是 "abc"、甚至是个 dict，都可能出现在伪造/历史 token 里
    if not isinstance(subject, str) or not subject.isdigit():
        raise _credentials_exception()

    # db.get 就是 SELECT ... WHERE id = ?（走主键索引），SQLAlchemy 2.0 的写法；
    # 不要用 db.query(User).get()，那是 1.x 遗留 API
    user = db.get(User, int(subject))
    if user is None:
        # 用户被删了但 token 还没过期 —— 这正是"每次回查库"的价值
        raise _credentials_exception()
    if not user.is_active:
        raise _credentials_exception()

    return user


# 路由里统一用这个别名，比每处写 Annotated[User, Depends(get_current_user)] 短，
# 也保证"改一处，所有接口生效"。用法：
#     async def read_me(user: CurrentUser) -> UserOut: ...
CurrentUser = Annotated[User, Depends(get_current_user)]
