"""数据库连接层（知识点 8）。

只负责三件事：建引擎、建会话工厂、建 Base，以及一个请求级的 get_db()。
本文件不含任何表模型（User / Conversation / Message 下一节写）。

三个对象的关系（拿 Spring 类比）：

    engine        ≈ DataSource        连接池 + 数据库方言，进程内一个
    SessionLocal  ≈ SessionFactory    会话工厂，调用一次产出一个 Session
    Session       ≈ SqlSession        工作单元，负责 flush / commit / rollback
    Base          ≈ 实体基类          只承载 metadata（表结构登记簿）

所以 engine 全局唯一、Session 每请求一个，Base 只在迁移时被 Alembic 读取。

建表已统一交给 Alembic（alembic upgrade head），本文件不再提供 create_all 入口，
详见文件末尾 init_db() 的说明。
"""

from collections.abc import Generator
from typing import Any

from sqlalchemy import MetaData, create_engine, event
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings


def _connect_args(database_url: str) -> dict[str, Any]:
    """按数据库类型生成 connect_args（解决"SQLite 跨线程报错"问题）。

    SQLite 驱动默认要求"创建连接的线程 == 使用连接的线程"（check_same_thread=True）。
    FastAPI 的同步路由跑在线程池里，连接可能被不同 worker 线程复用，按官方文档
    会遇到 `SQLite objects created in a thread can only be used in that same thread`。

    说明：这项检查是否真的触发，取决于 Python 的 sqlite3.threadsafety 与 SQLite
    的编译选项（本机 3.13 的构建是 threadsafety=3，实测跨线程复用连接并不报错），
    但在 3.11/3.9 的常见发行版上会报。成本只是一个参数，没必要赌部署环境。

    另外这个参数只有 sqlite 的 DBAPI 认，PostgreSQL / MySQL 驱动收到它会直接抛
    TypeError（unexpected keyword argument），所以必须按方言判断，
    不能图省事对所有数据库都传。

    Args:
        database_url: 形如 sqlite:///./app.db、postgresql+psycopg://...

    Returns:
        dict[str, Any]: SQLite 返回 {"check_same_thread": False}，其它返回空字典。
    """
    # 用 make_url 取 backend name，比 startswith("sqlite") 稳：
    # sqlite+pysqlite://、sqlite+aiosqlite:// 这类带驱动名的写法同样能识别
    backend = make_url(database_url).get_backend_name()
    if backend == "sqlite":
        return {"check_same_thread": False}
    return {}


# ---------------------------------------------------------------------------
# 引擎：全进程一个，内部维护连接池
# ---------------------------------------------------------------------------
engine: Engine = create_engine(
    settings.DATABASE_URL,
    # echo 由配置控制，不要写死 True：生产每执行一条 SQL 都打日志，
    # 既拖性能又会把参数值（手机号、消息正文）写进日志文件
    echo=settings.SQL_ECHO,
    connect_args=_connect_args(settings.DATABASE_URL),
    # 取连接前先 ping。数据库重启或连接被中间件掐断后，池里的死连接会被换掉，
    # 避免重启后的第一个请求必报 "server closed the connection unexpectedly"
    pool_pre_ping=True,
)


# ---------------------------------------------------------------------------
# SQLite 外键开关
# ---------------------------------------------------------------------------
@event.listens_for(engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection: Any, connection_record: Any) -> None:
    """在每条 SQLite 连接上打开外键约束（解决"本地不报错、上线才炸"问题）。

    SQLite 默认 foreign_keys=OFF，外键定义形同注释：能插入指向不存在用户的会话，
    也能留下会话已删、消息还在的孤儿行。开发期全绿，一换 PostgreSQL 立刻
    IntegrityError —— 这种"只在生产出现"的问题最难查。

    这里在每次建连时执行 PRAGMA foreign_keys=ON，让本地行为和生产一致；
    其它数据库本身强制外键，不需要这段。

    Args:
        dbapi_connection: 底层 DBAPI 连接。
        connection_record: SQLAlchemy 的连接池记录（未使用，事件签名要求）。
    """
    if engine.dialect.name != "sqlite":
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


# ---------------------------------------------------------------------------
# 会话工厂：不是会话本身，调用一次产出一个 Session
# ---------------------------------------------------------------------------
SessionLocal: sessionmaker[Session] = sessionmaker(
    bind=engine,
    # 显式关闭自动提交与自动 flush：事务边界必须由业务代码掌握，
    # 否则一次 commit 会把没准备好校验的数据也写进库
    autocommit=False,
    autoflush=False,
    # commit 后不过期对象属性。否则 FastAPI 序列化响应时触发惰性加载，
    # 而那时会话可能已关闭，就会抛 DetachedInstanceError
    expire_on_commit=False,
)


# ---------------------------------------------------------------------------
# Base：所有 ORM 模型的父类，只承载 metadata
# ---------------------------------------------------------------------------
class Base(DeclarativeBase):
    """ORM 声明式基类。

    子类写成 `class User(Base)` 后，表结构会自动登记到 Base.metadata，
    alembic/env.py 的 `target_metadata = Base.metadata` 就是从这份登记簿里读的，
    autogenerate 全靠它比对差异。
    """

    # 统一约束命名。SQLite 会给约束生成匿名名字（如 UNIQUE (email)），
    # 而 SQLite 本身不支持 drop constraint，Alembic 在它上面就写不出降级脚本；
    # 提前约定命名规则，迁移到 PostgreSQL 时也省掉一堆改名 SQL。
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(column_0_label)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


# ---------------------------------------------------------------------------
# 请求级会话依赖
# ---------------------------------------------------------------------------
def get_db() -> Generator[Session, None, None]:
    """为每个请求提供一个数据库会话，请求结束后必关闭（解决"连接泄漏"问题）。

    归属说明：这个依赖的**正式归宿是 app/deps.py**，本节先放在 database.py，
    是为了让 engine / SessionLocal / Session 三个概念在一个文件里就能读完。
    目前 app/deps.py 里是 `from app.database import get_db` 的转发，全项目
    只有这一份 try/finally；知识点 9 若想把它挪回 deps.py，把函数搬过去、
    让 database 反向导入即可，注意别两边各留一份（两份迟早会分叉）。

    必须是 yield 依赖，不能把会话对象直接 return 出去：依赖返回后 FastAPI
    就没有机会再执行清理代码，连接不归还，池子打满（默认 5 + 10）之后
    新请求会卡在 checkout，表现为接口全部挂起 30 秒再统一 500。

    Yields:
        Session: 当前请求独占的数据库会话。
    """
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        # 即使路由抛异常也会走到这里，close 会把连接归还连接池。
        # 注意 close != rollback：未提交的事务在连接归还时由 reset 回滚，
        # 但显式 rollback 语义更清楚，业务里出错还是自己 rollback 一次。
        db.close()


def init_db() -> None:
    """已停用：建表统一交给 Alembic，这里只剩一句明确的报错。

    原来这里是 `Base.metadata.create_all(bind=engine)`。接上 Alembic 之后必须去掉，
    否则会出现两种"看起来正常"的坏情况：

    1. **版本表对不上**。create_all 建出的表结构是新的，但 alembic_version 里
       一条记录都没有。之后 `alembic upgrade head` 会从头跑一遍第一个迁移，
       报 `table users already exists`；或者你以为迁移生效了，其实一行都没跑。
    2. **改了字段不生效**。create_all 只"建缺失的表"，对已存在的表什么都不做 ——
       给 User 加一列，本地跑一次 init_db 毫无变化，你会以为代码没保存。

    保留这个函数（而不是直接删）是为了让旧的调用点立刻失败并拿到正确指引，
    而不是 `ImportError: cannot import name 'init_db'` 这种要翻代码才知道原因的错误。

    Raises:
        RuntimeError: 恒定抛出，提示改用 Alembic 命令。
    """
    raise RuntimeError(
        "init_db() 已停用：建表/改表请使用 Alembic\n"
        "  首次建库：alembic upgrade head\n"
        "  改完模型：alembic revision --autogenerate -m \"描述\" && alembic upgrade head"
    )
