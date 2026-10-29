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

from pydantic import field_validator, model_validator
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
    # 只影响 /docs 标题、OpenAPI 信息与启动日志，改它不改变任何行为。
    # 版本号不放在这里：唯一事实来源是 app/__init__.py 的 __version__，
    # 两处各写一个版本号，迟早出现"docs 显示 0.2、代码是 0.3"的分叉。
    APP_NAME: str = "AI Chat Backend"

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

    # 调试总开关。为 True 时会额外挂载 /api/v1/debug/* 路由（故意打日志、
    # 故意抛异常之类）。生产必须为 False —— 调试接口是最容易被忽略的后门。
    DEBUG: bool = False

    # ---- 日志（知识点 14）----
    # 级别：DEBUG / INFO / WARNING / ERROR / CRITICAL。
    # 放在配置里而不是写死在代码中，是为了"线上临时开 DEBUG 不用改代码重新构建镜像"：
    # 改环境变量重启容器即可，镜像保持不可变。
    LOG_LEVEL: str = "INFO"
    # true 时输出单行 JSON（给 ELK / Loki / CloudWatch 采集用），
    # false 时输出人读的竖线分隔格式。容器里通常两者都要：
    # 开发本地用 console，线上用 json。
    LOG_JSON: bool = False
    # 彩色输出开关。None = 自动判断（stdout 是 TTY 才上色），
    # True/False = 强制开/关。容器里 stdout 不是 TTY，自动就是关的。
    LOG_COLOR: bool | None = None

    # ---- CORS ----
    # 用逗号分隔的字符串而不是 list[str]：pydantic-settings 对复杂类型
    # 会按 JSON 解析环境变量，写成 `CORS_ORIGINS=["a","b"]` 很容易因为
    # 引号/转义在部署时炸掉。字符串 + split 是最不容易出错的形态。
    CORS_ORIGINS: str = "http://localhost:3000,http://localhost:8501"

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

    # ---- LLM 上游（知识点 15）----
    # OpenAI 兼容接口的根地址；DeepSeek / 通义 / 自建网关都吃这套路径
    LLM_BASE_URL: str = "https://api.deepseek.com/v1"
    # 默认留空：宁可调用时报"未配置"，也不要在代码里塞一个假 key ——
    # 假 key 会让启动看起来一切正常，直到第一个真实请求才 401
    LLM_API_KEY: str = ""
    # 超时（秒）。聊天接口是同步等待，超时太长会把线程池占满
    LLM_TIMEOUT_SECONDS: float = 30.0
    # 假模型开关：true 时不发任何网络请求，直接把用户最后一句话回显。
    # 用途是 scripts/smoke.sh 与本地联调 —— 没有 API Key 的机器也能跑通
    # "注册→登录→建会话→对话→删" 整条链路。生产必须 false：
    # 开着它服务永远不会真调模型，但一切看起来都正常，属于最难发现的那类配置事故。
    LLM_MOCK: bool = False

    @field_validator("LOG_LEVEL")
    @classmethod
    def _normalize_log_level(cls, value: str) -> str:
        """把日志级别规整成大写，并在启动阶段挡掉拼错的值。

        Args:
            value: 环境变量 LOG_LEVEL 的原始值。

        Returns:
            str: 大写后的级别名。

        Raises:
            ValueError: 传了 logging 不认识的级别。

        Note:
            必须显式校验。`logging.setLevel("INFOO")` 在 Python 3.11 会抛
            ValueError，但 `setLevel("10")` 这类字符串数字会被当成 0 处理 ——
            结果是**日志全部消失**，而服务看起来一切正常。
            这种"静默失声"比启动失败难查一百倍，所以宁可起不来。
        """
        level = value.strip().upper()
        valid = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if level not in valid:
            raise ValueError(f"LOG_LEVEL 必须是 {sorted(valid)} 之一，收到 {value!r}")
        return level

    @property
    def cors_origins(self) -> list[str]:
        """把逗号分隔的 CORS_ORIGINS 拆成列表。

        Returns:
            list[str]: 去掉空白与空项后的来源列表。
        """
        return [item.strip() for item in self.CORS_ORIGINS.split(",") if item.strip()]

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
            # DEBUG 会挂载 /api/v1/debug/*（能主动打日志、抛异常、暴露内部状态），
            # 生产环境开着它等于给攻击者一个自助探测面板
            if self.DEBUG:
                raise ValueError("生产环境禁止 DEBUG=True，调试路由不应暴露在公网")
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
