"""密码哈希与 JWT 签发 / 校验（知识点 12）。

===============================================================================
⚠️  SECRET_KEY 必须从环境变量来，泄露等于任何人都能伪造 token
===============================================================================

本项目用的是 HS256（HMAC-SHA256），它是对称算法：**签发和校验共用同一把密钥**。
这一条决定了两件事：

1. 任何拿到 SECRET_KEY 的人，不需要数据库、不需要用户密码，直接

       jwt.encode({"sub": "1", "exp": ...}, SECRET_KEY, algorithm="HS256")

   就能造出一条我们服务端完全认可的 token，冒充任意用户（包括管理员）。
   攻击者不会"表现得像入侵"，他就是一个合法登录的用户，日志里看不出异常。

2. 所以 SECRET_KEY 的地位等于**数据库 root 密码**：

   - 绝不写进代码、绝不写进默认值、绝不提交到 git（.env 已在 .gitignore）；
   - 由部署环境注入（K8s Secret / 云厂商 Secret Manager / Vault），
     而不是 `docker build` 时 COPY 进镜像 —— 镜像会被推送到 registry，
     留在镜像层里的密钥等于公开；
   - 不打印、不打日志、不进异常栈（本文件所有日志/异常文案都不带它）；
   - 泄露后的处置只有一件事：**立刻换密钥**。换掉后所有已签发 token 立即失效
     （用户需要重新登录），这是唯一有效的止损手段。
     想让"换密钥不完全踢人"就得引入 kid + 多密钥轮换，那是另一套复杂度。

配置侧还有一道保险：app/config.py 的 _guard_secret_key 会在 ENV=prod 且密钥
仍是默认值 / 长度不足 32 字符时**直接拒绝启动**。宁可服务起不来，
也不要带着一个人人皆知的密钥对外提供服务。
"""

from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from passlib.context import CryptContext

from app.config import settings

# ---------------------------------------------------------------------------
# 密码哈希
# ---------------------------------------------------------------------------
# CryptContext 而不是直接调 bcrypt.hashpw 的理由：
#   - 算法可演进：以后加 argon2 只需改 schemes 顺序（["argon2", "bcrypt"]），
#     老用户下次登录时 verify_and_update() 自动升级哈希，不用写迁移脚本；
#   - deprecated="auto" 让"除第一种之外"的方案自动标记为待升级，
#     新哈希永远用第一个方案，存量哈希继续可验证。
#
# bcrypt 的 cost 默认 12（约 200~300ms/次），这是**故意的**：
# 攻击者离线爆破时每一次猜测都要付出同样的代价。调低能省 CPU，但等于帮攻击者加速。
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# bcrypt 的硬限制：算法内部只使用密码的**前 72 字节**，超出部分被静默丢弃。
# 注意是"字节"不是"字符"：UTF-8 下一个汉字占 3 字节，所以 24 个汉字就到顶了。
BCRYPT_MAX_BYTES = 72

# 长度下限定 8：NIST SP 800-63B 建议下限 8，且**不要**强制复杂度规则
# （大小写+符号+数字的"组合拳"只会让人写出 Passw0rd! 这类可预测密码，
#  真正有效的是长度 + 黑名单 + 限流）。上限按**字节**算，见下方说明。
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = BCRYPT_MAX_BYTES


def check_password_length(password: str) -> str:
    """校验密码长度是否落在 bcrypt 能完整处理的范围内（8~72 字节）。

    为什么必须显式校验，而不是"反正 bcrypt 会截断"：
    ``pwd_context.hash("a" * 80)`` 不会报错，它只把前 72 字节拿去算哈希。
    于是 "a"*72 和 "a"*80 得到的哈希可以互相验证通过 ——
    用户以为自己设了一个 80 位的强密码，实际上**前 72 字节就是全部**，
    任何知道前 72 字节的人都能登录。更糟的是这种截断完全静默，
    在测试环境也发现不了（这也是为什么本函数不依赖调用方自觉）。

    Args:
        password: 用户提交的明文密码。

    Returns:
        str: 原样返回，便于在 Pydantic validator 里链式使用。

    Raises:
        ValueError: 少于 8 字节或多于 72 字节。
    """
    # 按字节而不是按字符判断：len("密码") == 2，但 len("密码".encode()) == 6。
    # 用字符数判断的话，一个 30 个汉字的中文密码（90 字节）会被放行然后悄悄截断。
    byte_length = len(password.encode("utf-8"))

    if byte_length < MIN_PASSWORD_LENGTH:
        raise ValueError(f"密码长度不能少于 {MIN_PASSWORD_LENGTH} 个字节")
    if byte_length > BCRYPT_MAX_BYTES:
        # 提示里同时给"字节"和"字符"两个口径，否则中文用户会疑惑
        # "我明明只输了 30 个字，为什么说太长"
        raise ValueError(
            f"密码长度不能超过 {BCRYPT_MAX_BYTES} 个字节"
            f"（纯英文约 {BCRYPT_MAX_BYTES} 个字符，汉字约 {BCRYPT_MAX_BYTES // 3} 个）"
        )
    return password


def hash_password(password: str) -> str:
    """把明文密码哈希成可入库的 bcrypt 串。

    Args:
        password: 明文密码，长度需在 8~72 字节之间。

    Returns:
        str: 形如 ``$2b$12$...`` 的哈希值（含算法标识、cost、盐），长度 60 字符。

    Raises:
        ValueError: 密码长度越界。见 check_password_length 的说明。

    Note:
        bcrypt 的盐是**随机生成并嵌在哈希串里**的，所以同一个密码每次哈希结果都不同，
        不需要（也不要）再单独建 salt 列。
        hashed_password 用 VARCHAR(256)，是为了将来换 argon2（约 100 字符）
        或 scrypt 时不必改表结构。
    """
    # 这里再校验一次而不是只依赖 Pydantic：这个函数可能被脚本、CLI、
    # 数据导入任务直接调用，绕过 HTTP 层。长度检查必须贴着"真正会截断的地方"。
    check_password_length(password)
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """校验明文密码与数据库里的哈希是否匹配。

    Args:
        plain_password: 用户本次输入的明文密码。
        hashed_password: 数据库里存的哈希串。

    Returns:
        bool: 匹配返回 True；不匹配、或哈希串本身损坏/算法未知时返回 False。

    Note:
        校验失败返回 False 而不是抛异常，是为了让调用方（登录接口）
        把所有失败情况收敛成同一个 401 响应。
        但这里**不做长度截断补偿**：用户输入超过 72 字节时 bcrypt 会截断后比对，
        所以 8~72 字节的限制必须在注册端就卡死。
    """
    try:
        return pwd_context.verify(plain_password, hashed_password)
    except ValueError:
        # passlib 对无法识别的哈希串抛 UnknownHashError（ValueError 的子类）。
        # 典型场景：历史数据用的是别的算法、手工改了库、字段被截断。
        # 这些都不该让登录接口 500 —— 认证失败就是认证失败。
        return False


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------
# 【为什么 payload 里只放 sub（user_id）和 exp，不放 username / email / role】
#
# 1) 体积：JWT 每个请求都随 Authorization 头走一遍（不是只发一次）。
#    username、email、nickname、role、avatar…… 加起来几百字节，
#    base64 后还要再涨约 1/3。塞进 Cookie 时更容易顶到 4KB 上限，
#    也更容易撞上某些网关的请求头长度限制。对弱网移动端尤其不划算。
#
# 2) 过期后数据不一致（这条才是关键）：
#    token 一旦签发就**冻结**了，服务端改不动它。假设往里放了 role="user"，
#    管理员把某人降权/封号后，他手里的 token 仍然写着 role="user"，
#    在 TTL（默认 60 分钟）内依然畅通无阻。
#    要让它"立即生效"，唯一的办法是每次请求回查数据库 ——
#    可一旦回查了数据库，token 里放这些字段就毫无意义：
#    数据的新鲜副本就在库里，何必再信一份可能过期的快照？
#    结论：token 只回答"你是谁"，"你能干什么"永远由服务端按当前数据决定。
#
# 3) 安全：JWT 的 payload 只是 base64url 编码，**不是加密**。
#    任何人拿到 token 都能解开看内容（浏览器插件、日志、崩溃上报、代理）。
#    把 email / 手机号塞进去就等于主动泄露 PII。
#
# 4) 单一事实来源：只放 user_id 时，改名、改邮箱、封号、删号全部"改一处立即生效"，
#    不存在"token 里还是旧值"这种需要额外同步的状态。
#
# 代价是每个请求多一次按主键的查询（走主键索引，可忽略），换来的是权限永远实时。
# 真需要"完全免查库"的场景（如边缘网关鉴权），才值得放 role，
# 并且必须接受"TTL 内的权限延迟"，同时把 TTL 压到 5~15 分钟 + 用 refresh token 续期。
#
# 【sub 必须是字符串】
# RFC 7519 规定 sub 的类型是 StringOrURI，PyJWT 2.10 起在 decode 时会强校验：
# 传整数会直接抛 InvalidSubjectError（不会帮你转成字符串）。
# 所以这里 encode 前 str(user_id)，decode 后再 int() 转回来。


def create_access_token(
    user_id: int,
    expires_delta: timedelta | None = None,
) -> str:
    """签发一条访问令牌。

    Args:
        user_id: 用户主键，会被写成 payload 里的 sub（字符串形式）。
        expires_delta: 自定义有效期；不传则用 settings.ACCESS_TOKEN_EXPIRE_MINUTES。
            单测里会传 timedelta(seconds=-1) 来造一条已过期的 token。

    Returns:
        str: 紧凑序列化的 JWT（header.payload.signature），可直接放进
        ``Authorization: Bearer <token>``。

    Note:
        exp 用 ``datetime.now(timezone.utc)`` 而不是老写法 utcnow：
        后者返回 naive datetime，转时间戳时会按**本机时区**解释，
        在一台时区为 UTC+8 的机器上编出来的 exp 会凭空差 8 小时 ——
        表现为"刚发出去的 token 立刻过期"或"本该过期却还能用"，
        而且只在特定时区的机器上复现，本地开发经常看不出来。
        PyJWT 会把 aware datetime 直接转成 UTC 时间戳，所以传 aware 才是对的。
    """
    now = datetime.now(timezone.utc)
    expire = now + (
        expires_delta
        if expires_delta is not None
        else timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )

    payload: dict[str, Any] = {
        # str() 不能省：见上方"sub 必须是字符串"
        "sub": str(user_id),
        # 过期时间。JWT 的 exp 是 UTC 秒级时间戳，客户端与服务端都只信它
        "exp": expire,
    }

    # 刻意不写 iat / jti / iss / aud：
    # 单服务、单受众时它们不带来安全收益，却会占体积；
    # 将来真要做"撤销单个 token"（jti 黑名单）或多服务受众校验（aud）时再加。
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_token(token: str) -> dict[str, Any] | None:
    """解析并校验 token，失败一律返回 None（不向上抛异常）。

    Args:
        token: 客户端放在 ``Authorization: Bearer <token>`` 里的原始串。

    Returns:
        dict[str, Any] | None: 校验通过时返回 payload（至少含 sub、exp）；
        签名错误、格式错误、已过期、缺 sub 等任何情况都返回 None。

    Note:
        - ``algorithms=[...]`` 必须显式传。不传时 PyJWT 会按 token header 里
          声明的 alg 去校验，攻击者把 header 改成 ``alg: none`` 或
          做 RS256→HS256 混淆（拿公钥当 HMAC 密钥）就能绕过签名；
        - ``options={"require": [...]}`` 保证 exp / sub 一定存在：
          没有 exp 的 token 等于**永不过期**，一旦泄露永远有效，
          这是 JWT 最经典的事故之一；
        - 只捕获 ``jwt.PyJWTError``（ExpiredSignatureError、InvalidSignatureError、
          DecodeError、InvalidSubjectError 都是它的子类）。
          不用裸 ``except Exception``：那会把 KeyError、编码错误之类
          真正的 bug 一起吞掉，排障时只剩一句"登录失败"。
    """
    try:
        return jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
            options={"require": ["exp", "sub"]},
        )
    except jwt.PyJWTError:
        # 故意不区分"过期"和"签名错误"，也不把异常细节带给调用方：
        # 对攻击者来说，"签名错"和"过期"是可以用来试探的两种不同反馈。
        return None
