"""应用配置（知识点 12 前置）。

所有可变参数集中在这里，用 pydantic-settings 从环境变量 / .env 读取。
业务代码只 import settings，不直接读 os.environ：

- 读 os.environ 散落在各处，改一个变量名就要全局搜索替换；
- 类型转换（"60" -> 60）没有地方统一做；
- 缺少必需变量时不会在启动阶段失败，而是等第一个请求进来才 500。

pydantic-settings 会在实例化时就校验类型和必填项，所以配置错误在
`uvicorn` 启动那一刻就会暴露，而不是线上跑了一半才炸。
"""

from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 开发用的默认密钥。生产环境启动时如果还是这个值，宁可起不来也不要跑起来
# —— 详见下方 _guard_secret_key。
_DEV_SECRET_KEY = "dev-secret-change-me"


class Settings(BaseSettings):
    """应用配置，字段名即环境变量名（大小写不敏感）。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        # 忽略 .env 里多余的自定义变量，避免部署环境多塞几个变量就启动失败
        extra="ignore",
    )

    # ---- 基础信息 ----
    APP_NAME: str = "AI Chat Backend"
    APP_VERSION: str = "0.1.0"

    # ---- 数据库 ----
    # TODO: 知识点 8 换成真实数据库地址（PostgreSQL: postgresql+psycopg://user:pwd@host/db）
    DATABASE_URL: str = "sqlite:///./ai_chat.db"
    # 是否把 SQLAlchemy 生成的 SQL 打到日志里；开发时设 true 排查 N+1 很好用，
    # 生产必须关掉（每条 SQL 一行日志，量非常大，还可能把参数值打进日志）
    SQL_ECHO: bool = False

    # ---- 运行环境 ----
    # dev / prod。只用来决定"要不要因为不安全的默认值直接拒绝启动"，
    # 不要拿它来切业务逻辑（那种事应该用 feature flag）
    ENV: str = "dev"

    # ---- 鉴权 ----
    # 服务间调用用的静态 API Key；生产环境必须通过环境变量注入，禁止用这里的默认值
    API_KEY: str = "dev-api-key-change-me"

    # ---- JWT（知识点 12）----
    # ⚠️ 必须从环境变量注入，泄露 = 任何人都能签发出合法 token。
    # HS256 是对称算法：签发和校验用**同一把**密钥，校验方天然具备签发能力。
    # 字段名刻意叫 SECRET_KEY 而不是 JWT_SECRET，是为了让 grep "SECRET" 能一把捞出来。
    SECRET_KEY: str = _DEV_SECRET_KEY
    # 只允许 HS256。**不要**把它做成"跟随 token 头声明的 alg"，
    # 那等于让攻击者用 alg=none 或 RS256/HS256 混淆自行选择校验方式。
    ALGORITHM: str = "HS256"
    # token 有效期（分钟）。短 TTL 是泄露后可利用窗口的上限：
    # 15~60 分钟是常见区间，超过一天基本等于"密码永不过期"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60

    @model_validator(mode="after")
    def _guard_secret_key(self) -> "Settings":
        """生产环境下拒绝使用开发默认密钥，把配置事故挡在启动阶段。

        为什么要 fail fast：如果线上用着 `dev-secret-change-me`，
        服务一切正常、日志也没有异常，但**任何读过本项目源码的人**都能
        自行签发一条 `{"sub": "1"}` 的 token 冒充任意用户。
        这种问题不会自己暴露，只会等到被利用那天。

        Returns:
            Settings: 校验通过的自身实例。

        Raises:
            ValueError: ENV 为 prod/production 且 SECRET_KEY 是默认值或长度不足 32。
        """
        if self.ENV.lower() in {"prod", "production"}:
            if self.SECRET_KEY == _DEV_SECRET_KEY:
                raise ValueError(
                    "生产环境禁止使用默认 SECRET_KEY，请通过环境变量注入 "
                    "（例如 `python -c \"import secrets; print(secrets.token_urlsafe(48))\"` 生成）"
                )
            # HS256 的安全性上限由密钥熵决定，32 字节（≥43 字符 base64）是下限，
            # 比这更短就有被离线暴力破解的可能
            if len(self.SECRET_KEY) < 32:
                raise ValueError("SECRET_KEY 长度不足 32 字符，HS256 密钥需要足够的熵")
        return self


@lru_cache
def get_settings() -> Settings:
    """构造并缓存配置单例。

    lru_cache 保证整个进程只解析一次 .env（包括磁盘 IO 和校验），
    同时让依赖注入里可以 `Depends(get_settings)` 拿到同一个对象。

    Returns:
        Settings: 进程级唯一的配置对象。
    """
    return Settings()


# 模块级单例，业务代码直接 `from app.config import settings` 使用
settings = get_settings()
