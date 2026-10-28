"""认证路由：注册 + 登录换 token（知识点 12）。

两个接口的分工：

- POST /api/v1/auth/register —— 创建用户，返回 201 + UserOut（不含任何哈希）
- POST /api/v1/auth/token    —— 表单登录（OAuth2 密码模式），返回 access_token

本文件有几处是**刻意**的写法，改之前先看注释：

1. 两个接口都用 `def` 而不是 `async def`；
2. 登录失败只给一种提示，不区分"用户不存在"和"密码错误"；
3. 用户不存在时也会跑一次假的密码校验（时间对齐，防用户名枚举）；
4. 密码哈希、JWT 的全部实现都在 app/auth.py，这里只做编排。

⚠️ SECRET_KEY 必须从环境变量来，泄露等于任何人都能伪造 token ——
签发/校验的细节与处置办法见 app/auth.py 顶部说明。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import create_access_token, hash_password, verify_password
from app.config import settings
from app.deps import get_db
from app.models import User
from app.schemas import TokenOut, UserCreate, UserOut

router = APIRouter(prefix="/auth", tags=["认证"])

# 登录失败的**唯一**提示文案。
# 不拆成"用户名不存在"/"密码错误"两种：那等于给攻击者一个免费的账号探测器 ——
# 先用常见用户名把库里的账号列出来，再针对这些账号集中撞库，
# 效率和成功率都比盲目爆破高一个数量级。
_LOGIN_FAILED = "用户名或密码错误"

# 时间对齐用的假哈希：用户不存在时拿它跑一次 verify_password。
# 不这么做的话，"用户不存在"（不查哈希，~1ms）和"密码错误"（bcrypt 校验，~250ms）
# 在响应时间上差了两个数量级 —— 攻击者不用看响应内容，掐表就能判断用户名是否存在。
# 这是纯粹的时序侧信道，日志里完全看不出来。
# 在模块导入时算一次（成本 ~250ms，只在启动时付）；
# 每次请求现算反而会多花一次哈希的时间，还更慢。
_DUMMY_HASH = hash_password("dummy-password-for-timing-equalization")


@router.post(
    "/register",
    response_model=UserOut,
    status_code=status.HTTP_201_CREATED,
    summary="注册新用户",
    responses={
        409: {"description": "用户名或邮箱已被占用"},
        422: {"description": "字段校验失败（如密码不足 8 字节、邮箱格式错误）"},
    },
)
def register(
    payload: UserCreate,
    db: Annotated[Session, Depends(get_db)],
) -> User:
    """创建新用户，返回脱敏后的用户信息。

    用普通 ``def`` 而不是 ``async def``：bcrypt 的哈希是**同步 CPU 密集**操作
    （cost=12，约 200~300ms）。写成 async def 会让它跑在事件循环线程上，
    一个注册请求就能把整个进程的其他请求全卡住 —— 10 个并发注册等于服务假死。
    ``def`` 会被 FastAPI 丢进线程池执行，事件循环照常处理别的请求。
    同理适用于任何调用 requests / bcrypt / PIL 的同步阻塞代码。

    Args:
        payload: 注册请求体，用户名/邮箱/密码已在 Pydantic 层校验过长度与格式。
        db: 数据库会话。

    Returns:
        User: 新建的用户 ORM 对象，经 response_model=UserOut 过滤后输出
        （hashed_password 不会出现在响应里）。

    Raises:
        HTTPException: 409，用户名或邮箱已被占用。
    """
    # 邮箱统一转小写再存：
    # 数据库的 UNIQUE 约束是**大小写敏感**的，不归一化就会出现
    # Alice@x.com 和 alice@x.com 两个"不同"的账号，而邮件系统认为它们是同一个人。
    # 用户名不转小写 —— 用户名是展示标识，且区分大小写是很多产品的既定行为。
    email = str(payload.email).lower()

    # 分开查、分开报错：注册场景下"哪个字段冲突"是必要的可用性信息
    # （否则用户只能反复猜），这里接受了这点枚举风险 —— 因为注册接口本身
    # 就必然暴露"某账号是否存在"，这是注册功能无法回避的固有属性。
    # 真要做严格防护，方案是：统一返回 202 + 发确认邮件，让攻击者拿不到即时反馈。
    if db.scalar(select(User.id).where(User.username == payload.username)) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"用户名 {payload.username!r} 已被占用",
        )
    if db.scalar(select(User.id).where(User.email == email)) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="该邮箱已被注册",
        )

    user = User(
        username=payload.username,
        email=email,
        # 只存哈希。明文密码在这行之后就不再存在于任何地方 ——
        # 不要为了"方便排查"把它打到日志里，日志的留存和访问控制通常比数据库松得多。
        hashed_password=hash_password(payload.password),
    )
    db.add(user)

    try:
        db.commit()
    except IntegrityError:
        # 上面的两次查询和这次 INSERT 之间存在竞态窗口（两个请求同时注册同名用户）。
        # 应用层的"先查再插"永远不能替代数据库的唯一约束，这里兜住最终防线：
        # 回滚掉失败的事务，返回和查询命中时一致的 409，而不是让 500 冒出去。
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="用户名或邮箱已被占用",
        ) from None

    # refresh 把数据库生成的值（自增 id、created_at 默认值）读回对象，
    # 否则序列化时 id 可能还是 None
    db.refresh(user)
    return user


@router.post(
    "/token",
    response_model=TokenOut,
    summary="登录并获取 access_token",
    responses={401: {"description": "用户名或密码错误"}},
)
def login(
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: Annotated[Session, Depends(get_db)],
) -> TokenOut:
    """用用户名（或邮箱）+ 密码换取 JWT。

    必须用 ``application/x-www-form-urlencoded`` 表单提交，不是 JSON：

        curl -X POST http://127.0.0.1:8000/api/v1/auth/token \\
             -d "username=alice&password=correct-horse-battery"

    这是 OAuth2 密码模式（RFC 6749 §4.3.2）规定的格式，FastAPI 的
    OAuth2PasswordRequestForm 和 Swagger UI 的 Authorize 按钮都按这个约定发请求。
    改用 JSON 的话，Swagger 的 Authorize 会直接失败（它不会帮你拼 JSON），
    而且违反规范 —— 这不是风格问题，是互操作性问题。
    依赖 python-multipart：没有它 FastAPI 在建路由时就会报错。

    Args:
        form: 表单字段。``username`` 这里同时接受用户名和邮箱，
            ``password`` 为明文密码；``grant_type`` 等其余字段忽略。
        db: 数据库会话。

    Returns:
        TokenOut: access_token（JWT）、token_type（bearer）、expires_in（秒）。

    Raises:
        HTTPException: 401，用户不存在、密码错误或账号被禁用 ——
            三种情况文案完全一致，见下方说明。

    Note:
        **没有做登录限流**。这是上线前的必修项：按 IP + 账号双维度限流
        （如 5 次/分钟，连续失败后指数退避 + 验证码），否则一份密码字典
        就足以对线上接口做在线撞库。实现建议放在网关或 slowapi 中间件，
        而不是写在这里 —— 限流是横切关注点，散在业务函数里必然漏。
"""
    identifier = form.username.strip()

    # 先按用户名查，再按邮箱查。两次都是走索引的单行查询，
    # 用 or_ 合并成一条反而会让优化器放弃索引（不同列的 OR）。
    user = db.scalar(select(User).where(User.username == identifier))
    if user is None:
        user = db.scalar(select(User).where(User.email == identifier.lower()))

    if user is None:
        # 关键：这里也要跑一次 bcrypt，把响应时间拉到和"密码错误"同一量级。
        # 结果直接丢弃 —— 我们只买它的耗时，不关心返回值。
        verify_password(form.password, _DUMMY_HASH)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_LOGIN_FAILED,
            # 401 必须给出认证方案，客户端据此知道该重新走 /auth/token
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not verify_password(form.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_LOGIN_FAILED,
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        # 账号被禁用时**也**返回同一句话，不提示"账号已禁用"。
        # 原因是：那条提示等于确认"这个用户名存在、且你猜对了密码"，
        # 攻击者可以据此验证撞库成果（甚至判断某账号是否被封）。
        # 禁用通知走运营渠道（邮件/客服/站内消息），不走登录接口。
        # 顺带说明：已签发 token 的失效不依赖这里 —— get_current_user
        # 每次都回查库并检查 is_active，所以禁用那一刻存量 token 就废了。
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_LOGIN_FAILED,
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token = create_access_token(user.id)
    return TokenOut(
        access_token=access_token,
        # 与 create_access_token 内部用的是同一个配置，避免"返回 3600 秒
        # 但 token 实际只有 15 分钟"这种前后端对不上的情况
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )
