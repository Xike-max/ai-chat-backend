"""Alembic 运行环境。

这份文件回答三个问题：**去哪儿找模型**、**连哪个库**、**怎么比差异**。

与官方模板相比只有四处改动，全部用 `# [改动 N]` 标出：
    1. 把项目根目录塞进 sys.path（否则 `import app.xxx` 必然 ModuleNotFoundError）
    2. 导入 Base 与 models（只导入 Base 不够，模型没被 import 过，
       metadata 里就是空的，autogenerate 会生成一个只有 pass 的空迁移）
    3. target_metadata = Base.metadata（这是"比对基准"，写 None 也是空迁移）
    4. 数据库地址从 settings 注入，不在 alembic.ini 里硬编码

另有两处 SQLite 专属处理，都是踩过坑才加的，见 _build_migration_engine
和 _configure_and_run 里的注释。
"""

from logging.config import fileConfig

# [改动 1] 一定要在 import app.* 之前把项目根目录加进 sys.path
import sys
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine, event, pool
from sqlalchemy.engine import Connection, Engine, make_url

PROJECT_ROOT = Path(__file__).resolve().parents[1]
# parents[1]：env.py -> alembic/ -> 项目根。
# 用 resolve() 拿到绝对路径，避免从别的目录执行 alembic 时算出相对路径。
# 虽然 alembic.ini 里的 prepend_sys_path = . 通常已经够用，但那只在
# "当前工作目录 == 项目根"时成立；CI 里从仓库根跑、或 IDE 配置了别的
# working directory，就会变成 ModuleNotFoundError: No module named 'app'。
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import models  # noqa: E402,F401  [改动 2] 副作用导入：把三张表登记进 metadata
from app.config import settings  # noqa: E402
from app.database import Base, _connect_args  # noqa: E402

# Alembic 的 Config 对象，对应 alembic.ini
config = context.config

# 按 ini 里的 [loggers] 配置日志；没有这一行，alembic 跑起来一行输出都没有
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# [改动 3] 比对基准：模型侧的 metadata
# 写成 None 时 autogenerate 不做任何比较，产出的脚本里只有一个 pass ——
# 这就是"迁移生成了但是空的"最常见原因。
target_metadata = Base.metadata

# [改动 4] 数据库地址从 settings 注入，ini 里只留一个被注释掉的占位
# 为什么不写在 alembic.ini：
#   - ini 由 ConfigParser 解析，值里的 % 会被当成插值语法，
#     URL 中出现 40% 编码的密码（p%40ss）就会直接抛 InterpolationSyntaxError；
#   - 两处配置两份真相，改 .env 忘了改 ini 是经典事故。
# 这里额外把 % 转义成 %%：ConfigParser 读出来时会还原，但直接写裸 % 会炸。
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL.replace("%", "%%"))


def _render_item(type_, obj, autogen_context):
    """把自定义类型渲染成标准的 sa.DateTime，避免迁移脚本 import 不到。

    `app.models.UTCDateTime` 是 TypeDecorator。Alembic 渲染列类型时取的是
    `repr(obj)`，于是生成的脚本里会出现 `sa.Column('created_at', UTCDateTime())`
    —— 而这里没有 import 过 UTCDateTime，upgrade 时直接 NameError。

    UTCDateTime 的 impl 就是 DateTime(timezone=True)，DDL 与它完全等价，
    所以在迁移脚本里渲染成内置类型即可，不必让迁移文件反向依赖业务代码。
    这也符合迁移脚本的原则：**脚本一旦提交就冻结**，不该随业务代码改动而变。

    Args:
        type_: "type" / "column" / "server_default"，这里只处理 type。
        obj: 具体对象。
        autogen_context: Alembic 渲染上下文。

    Returns:
        str | bool: 返回替换后的渲染字符串；返回 False 表示用默认渲染。
    """
    if type_ == "type" and obj.__class__.__name__ == "UTCDateTime":
        return "sa.DateTime(timezone=True)"
    return False


def _build_migration_engine() -> Engine:
    """为迁移单独建一个引擎（解决 SQLite batch 模式删数据的问题）。

    为什么不用应用那个 engine：
    迁移需要在连接级别关掉外键强制（见下面的事件），而应用引擎的每条连接
    都应该**保持**外键强制，两者诉求相反。用独立的 NullPool 引擎，
    PRAGMA 只作用于迁移进程自己的连接，不会通过连接池漏给应用请求。

    为什么要关外键强制：
    `render_as_batch=True` 下，SQLite 加列/删列是「建临时表 -> 搬数据 ->
    DROP 原表 -> RENAME」。而 DROP TABLE 在 foreign_keys=ON 时会对子表
    触发 ON DELETE CASCADE —— 删 conversations 的表结构，messages 的行
    跟着一起没。实测：downgrade 一次，messages 从 2 行变 0 行，
    全程没有一条报错，alembic_version 也正常回退，只有事后查数据才发现。

    为什么用 connect 事件而不是 exec_driver_sql：
    PRAGMA foreign_keys 在事务内设置会被静默忽略；而 SQLAlchemy 2.0 的
    `connection.exec_driver_sql(...)` 会触发 autobegin，把连接开进一个事务，
    结果既没关掉外键，还破坏了 Alembic 对版本号的提交时机
    （实测表现为：表建出来了，alembic_version 却是空的）。
    connect 事件在驱动建立连接的那一刻执行，此时一定没有事务。

    Returns:
        Engine: 迁移专用引擎。
    """
    database_url = settings.DATABASE_URL
    engine = create_engine(
        database_url,
        # 迁移是短命进程，连接不复用，也不会残留半开事务
        poolclass=pool.NullPool,
        # 与应用完全一致的方言参数（SQLite 的 check_same_thread 等）
        connect_args=_connect_args(database_url),
    )

    if make_url(database_url).get_backend_name() == "sqlite":
        @event.listens_for(engine, "connect")
        def _disable_foreign_keys(dbapi_connection, connection_record) -> None:
            """每条新连接建立时关掉外键强制（迁移期间由 batch 模式重建表）。"""
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=OFF")
            cursor.close()

    return engine


def run_migrations_offline() -> None:
    """离线模式：只生成 SQL 文本，不连数据库（产出交给 DBA 审）。"""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：连上数据库，逐个版本执行 upgrade/downgrade。"""
    connectable = _build_migration_engine()

    with connectable.connect() as connection:
        _configure_and_run(connection)

    # NullPool 下连接已随 with 关闭，这里只是让引擎释放自身引用
    connectable.dispose()


def _configure_and_run(connection: Connection) -> None:
    """按当前方言配置上下文并执行迁移。

    Args:
        connection: 已建立的数据库连接（外键强制已被事件关掉，见上）。
    """
    is_sqlite = connection.dialect.name == "sqlite"

    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # 类型变化也要被检测到（1.12 起默认 True，显式写出来是为了让意图可见）。
        # 关掉它的话，把 String(50) 改成 String(120) 不会生成任何 ALTER。
        compare_type=True,
        # SQLite 不支持大部分 ALTER TABLE（改类型、加约束、删列在旧版本上也不行），
        # 打开 batch 模式后 alembic 会用"建新表 -> 拷数据 -> 换名"来模拟。
        # 仅对 SQLite 打开：PostgreSQL 上走原生 ALTER，batch 反而多余。
        # 它必须和 _build_migration_engine 里的 PRAGMA foreign_keys=OFF 配对使用。
        render_as_batch=is_sqlite,
        # 把自定义类型渲染成内置类型，见 _render_item
        render_item=_render_item,
    )

    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
