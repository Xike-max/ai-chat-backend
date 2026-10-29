"""pytest 共享夹具（知识点 15）。

测试体系的三条硬规矩，全部体现在这个文件里：

1. **不碰开发库**。用例用的是 `sqlite://`（纯内存），进程结束就没了。
   用 `sqlite:///./ai_chat.db` 跑测试会有三种后果：污染本地数据、
   第二次跑同一个注册用例必 409、以及"别人机器上过、你这儿不过"
   —— 因为库里躺着他上次跑剩下的 alice。
2. **每个用例一个全新的库**。`create_all` / `drop_all` 包在 fixture 里，
   用例之间零共享状态，所以**没有任何用例依赖执行顺序**，
   单独 `pytest tests/test_chat.py::test_xxx` 也能过。
3. **不出网**。`mock_llm` 把 `get_llm_client` 换成返回固定文本的假客户端：
   测试不慢、不抖、不花钱。

为什么是 `StaticPool`（最容易踩的一脚）
--------------------------------------
`sqlite://` 是"每个连接一个独立的空库"。默认连接池会给每个会话开新连接，
于是 `create_all` 建表用的是 A 连接，请求里 `get_db` 用的是 B 连接 ——
B 看到的库是空的，报 `no such table: users`。
`StaticPool` 让整个引擎只维护**一条**连接并反复复用，表和数据才在同一份内存里。

（另一个常见解法是临时文件库 + 用完删文件，但那要处理 Windows 上的
文件占用与残留，内存库干净得多。）
"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

# 必须在 create_all 之前导入模型：Base.metadata 是"登记簿"，
# 模型类没被导入过，表就不会登记，create_all 会安静地建出 0 张表
from app import models  # noqa: F401
from app.database import Base
from app.deps import get_db
from app.llm import get_llm_client
from app.main import app

# 故意用模块级常量：用例里的期望值（用户名、密码）和夹具保持同一来源，
# 改一处即可，不会出现"夹具注册 alice、用例登录 alice2"
TEST_USERNAME = "alice"
TEST_EMAIL = "alice@example.com"
TEST_PASSWORD = "correct-horse-battery"

# 第二个用户：专门用来验越权（读/写别人的会话必须是 404）
OTHER_USERNAME = "bob"
OTHER_EMAIL = "bob@example.com"


@pytest.fixture(scope="session", autouse=True)
def _fast_password_hashing() -> Iterator[None]:
    """把测试进程里的 bcrypt cost 降到 4，让整个套件跑在 1 秒级。

    生产用的 12 轮单次约 250ms，一个"注册 + 登录"就是 500ms；15 个用例
    光算哈希就要 7 秒，测试一慢就没人愿意在提交前跑。cost=4 单次 1ms 左右，
    而**哈希格式完全一样**（cost 写在哈希串里），校验逻辑该验的照验。

    安全性说明：这只影响测试进程，`app/auth.py` 里的 CryptContext 仍是 12 轮。
    绝不能为了让测试变快去改生产配置 —— 那等于把 bcrypt 降级成免费礼物。

    Yields:
        None: 会话结束后把 pwd_context 换回原对象。
    """
    from passlib.context import CryptContext

    import app.auth as auth_module

    original = auth_module.pwd_context
    # bcrypt__rounds 是 passlib 的算法专有参数命名法（<scheme>__<option>）
    auth_module.pwd_context = CryptContext(schemes=["bcrypt"], bcrypt__rounds=4)
    try:
        yield
    finally:
        auth_module.pwd_context = original


@pytest.fixture()
def db_engine() -> Iterator[Engine]:
    """为每个用例建一个独立的内存库（建表 → 交给用例 → 拆表 → 销毁引擎）。

    Yields:
        Engine: 绑定了 StaticPool 的内存 SQLite 引擎。
    """
    engine = create_engine(
        # "sqlite://" 三个斜杠后面为空 = 纯内存库。
        # 注意不是 "sqlite:///:memory:" —— 后者在某些 SQLAlchemy 版本下
        # 会退化成"每个连接一个库"，症状和默认连接池一样
        "sqlite://",
        # FastAPI 的同步路由跑在线程池里，SQLite 默认禁止跨线程复用连接
        connect_args={"check_same_thread": False},
        # 关键：整个引擎只保留一条连接，create_all 和请求共用同一份内存库。
        # 不加它 → 每个连接一个新空库 → "no such table: users"
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        """打开 SQLite 外键约束，让本地行为和 PostgreSQL 一致。

        Args:
            dbapi_connection: 底层 DBAPI 连接。
            _connection_record: 连接池记录（未使用，事件签名要求）。
        """
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        # 先拆表再销毁引擎：StaticPool 的连接要显式释放，
        # 否则 Windows 上偶尔会留下未关闭的句柄（内存库本身不落盘，不至于脏数据）
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture()
def session_factory(db_engine: Engine) -> sessionmaker[Session]:
    """提供与本用例内存库绑定的会话工厂。

    Args:
        db_engine: 内存库引擎。

    Returns:
        sessionmaker[Session]: 与生产 SessionLocal 同参数（不自动 flush / commit）。
    """
    return sessionmaker(
        bind=db_engine,
        autocommit=False,
        autoflush=False,
        # 与 app/database.py 保持一致：commit 后不过期对象，
        # 否则响应序列化时可能触发已关闭会话的惰性加载
        expire_on_commit=False,
    )


@pytest.fixture()
def client(session_factory: sessionmaker[Session]) -> Iterator[TestClient]:
    """提供把数据库依赖指向内存库的 TestClient。

    `app.dependency_overrides[get_db]` 是 FastAPI 官方给的替换点：
    它按**函数对象**匹配，所以路由里 `Depends(get_db)` 拿到的就是我们这个
    覆盖版本，业务代码一行都不用改。

    Args:
        session_factory: 绑定内存库的会话工厂。

    Yields:
        TestClient: 走完整 ASGI 栈（含中间件、异常处理器）的测试客户端。
    """

    def override_get_db() -> Iterator[Session]:
        """用测试会话替换生产会话（每个请求一个，用完关闭）。

        Yields:
            Session: 绑定内存库的会话。
        """
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    # with 语句会触发 lifespan（启动/关闭日志），
    # 不用 with 的话 TestClient 不会跑 lifespan，某些初始化就漏了
    with TestClient(app) as test_client:
        yield test_client
    # 必须清空：覆盖是挂在**全局 app 对象**上的，不清会漏给下一个用例，
    # 表现为"单独跑能过、一起跑就串库"
    app.dependency_overrides.clear()


def register_and_login(
    client: TestClient,
    username: str = TEST_USERNAME,
    email: str = TEST_EMAIL,
    password: str = TEST_PASSWORD,
) -> dict[str, str]:
    """注册并登录一个用户，返回可直接用于请求头的鉴权字典。

    Args:
        client: 测试客户端。
        username: 用户名。
        email: 邮箱。
        password: 明文密码（8~72 字节）。

    Returns:
        dict[str, str]: 形如 {"Authorization": "Bearer <token>"}。

    Raises:
        AssertionError: 注册或登录没返回预期状态码（夹具坏了要立刻炸，
            而不是让十几个用例一起报"401 不是 200"这种误导性的错）。
    """
    register = client.post(
        "/api/v1/auth/register",
        json={"username": username, "email": email, "password": password},
    )
    assert register.status_code == 201, register.text

    login = client.post(
        "/api/v1/auth/token",
        # 必须是表单（OAuth2 密码模式），不是 JSON
        data={"username": username, "password": password},
    )
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


@pytest.fixture()
def auth_headers(client: TestClient) -> dict[str, str]:
    """注册并登录主测试用户，返回 Bearer 头，供需要鉴权的用例复用。

    Args:
        client: 测试客户端。

    Returns:
        dict[str, str]: {"Authorization": "Bearer ..."}。
    """
    return register_and_login(client)


# 假客户端返回的固定文本：用例断言它，就能确认"回复来自 LLM 层"，
# 而不是路由里随手拼的字符串
FAKE_REPLY = "这是测试用的固定回复：向量数据库用于存储与检索高维向量。"


class FakeLLMClient:
    """测试替身：记录调用参数、返回固定文本，绝不发网络请求。

    Attributes:
        calls: 每次 chat() 收到的 payload 列表，用于断言"历史消息确实拼进去了"。
        reply: 固定回复文本。
        error: 非 None 时 chat() 直接抛出它，用于模拟上游故障。
    """

    def __init__(self) -> None:
        """初始化空的调用记录。"""
        self.calls: list[dict] = []
        self.reply: str = FAKE_REPLY
        self.error: Exception | None = None

    def chat(self, payload: dict) -> str:
        """假装调用模型。

        Args:
            payload: 路由拼好的请求体。

        Returns:
            str: 固定回复。

        Raises:
            Exception: 当 error 被设置时抛出，模拟超时 / 5xx。
        """
        self.calls.append(payload)
        if self.error is not None:
            raise self.error
        return self.reply


@pytest.fixture()
def mock_llm(client: TestClient) -> Iterator[FakeLLMClient]:
    """把 get_llm_client 换成假客户端，保证测试不出网、不花钱。

    依赖 `client` 是为了保证覆盖在同一个 app 实例上、且在 client 的
    teardown（会 clear 所有 override）之前生效。

    Args:
        client: 测试客户端（仅用于保证夹具顺序）。

    Yields:
        FakeLLMClient: 可断言 calls、可注入 error 的假客户端。
    """
    fake = FakeLLMClient()
    # lambda 是合法的依赖：FastAPI 只看返回类型/被覆盖的键，不关心是不是函数定义
    app.dependency_overrides[get_llm_client] = lambda: fake
    try:
        yield fake
    finally:
        # 只弹自己这一个键，不用 clear()：clear 会把 client 夹具的 get_db
        # 覆盖一起清掉，导致同一次 teardown 里后续操作连到真实库
        app.dependency_overrides.pop(get_llm_client, None)
