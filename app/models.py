"""ORM 模型（知识点 9）。

三张表：users / conversations / messages，关系是
User 1—N Conversation 1—N Message。

两条硬规矩，后面所有代码都依赖它们：

1. **所有查询都必须带 user_id 过滤**。主键 id 只是数据库内部的定位符，
   不是访问凭证；`WHERE id = ?` 这种写法在 SaaS 里等于把别人的数据敞开。
   模型层给出的是"能力"，越权防线在 crud 层（见 crud.get_conversation）。
2. **时间一律 timezone-aware**。默认值统一走 utc_now()（内部是
   datetime.now(timezone.utc)），不要用老的 utcnow：它返回 naive datetime，
   序列化出来没有时区后缀，前端按本地时区解释，直接差 8 小时。
"""

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    TypeDecorator,
    func,
    select,
)
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import Mapped, column_property, mapped_column, relationship

from app.database import Base


class UTCDateTime(TypeDecorator[datetime]):
    """带时区的 DateTime：写入统一转 UTC，读出补回 tzinfo。

    为什么不直接写 DateTime(timezone=True)：
    PostgreSQL 的 TIMESTAMPTZ 会把时区存下来，读回来是 aware；
    但 **SQLite 的 DATETIME 只是一串文本，没有时区概念**，读回来永远是 naive。
    于是同一个字段，开发环境返回 "2026-10-28T15:05:27"（无 Z），
    生产返回 "2026-10-28T15:05:27+00:00" —— 前端 new Date() 前者按本地时区解释，
    正好差 8 小时，而且只在本地复现不了的时候才在线上暴露。

    这里把"存的时候转 UTC、读的时候补 UTC"收在一处：
    - impl 仍是 DateTime(timezone=True)，建表 DDL 不变（DATETIME / TIMESTAMPTZ）；
    - 读出后 tzinfo 恒为 UTC，Pydantic 序列化稳定输出 Z 结尾。

    cache_ok=True 表示这个类型对同一参数总是生成相同 SQL，允许语句缓存复用。
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        """写入前统一转成 UTC（naive 视为 UTC，不做本地时区猜测）。"""
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        """读出后补回 UTC 时区（SQLite 拿回来的是 naive）。"""
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


def utc_now() -> datetime:
    """返回当前 UTC 时间（带时区）。

    Returns:
        datetime: timezone-aware 的当前时间，存库和序列化都不会丢时区。

    Note:
        老写法 utcnow 返回的是 naive datetime，已在全项目禁用。
        用函数而不是 `default=lambda: datetime.now(timezone.utc)` 的好处：
        默认值可复用、可单测，Alembic 看到的也是同一个可调用对象。
    """
    return datetime.now(timezone.utc)


class User(Base):
    """用户表。"""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # unique + index：两者都要，前者保证约束，后者让按 username 登录的查询走索引
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    email: Mapped[str] = mapped_column(String(120), unique=True, index=True, nullable=False)
    # 只存哈希，永不存明文；长度 256 是为了放得下 argon2/bcrypt 的完整串
    hashed_password: Mapped[str] = mapped_column(String(256), nullable=False)
    # 账号是否可用。禁用走"置 False"而不是删行：
    # 删了行，他的会话/审计记录要么跟着级联消失，要么变成孤儿；
    # 而且删了之后再有人注册同一个用户名，历史日志就对不上人了。
    # 禁用后 get_current_user 会抛 401，存量 token 立即失效（因为每次都回查库）。
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utc_now, nullable=False
    )

    # 一个用户多个会话。cascade 让删除用户时一并清掉会话，
    # 否则会留下 user_id 指向不存在用户的孤儿行（SQLite 默认不校验外键）
    conversations: Mapped[list["Conversation"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )


class Conversation(Base):
    """会话表（一个用户的多条对话线程）。"""

    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # ondelete="CASCADE" 是数据库层保险：生产 PostgreSQL 会真的执行级联，
    # 万一有代码用 execute(delete(...)) 绕过 ORM，也不会留下孤儿消息
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    title: Mapped[str] = mapped_column(String(100), nullable=False)
    # system_prompt 可空：不传就用服务端默认人格，不该强制用户填
    system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 会话摘要：由模型异步生成，未生成时为 NULL。
    # 必须可空且**不给 server_default** —— 给已有数据表加 NOT NULL 列时，
    # 数据库没有任何值可以填给存量行，迁移会直接失败（表为空时才侥幸通过）。
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utc_now, nullable=False
    )
    # onupdate=utc_now：任何 UPDATE 都自动刷新，不依赖业务代码记得改
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utc_now, onupdate=utc_now, nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="conversations")
    # 注意这里**不加** passive_deletes=True：
    # 加了它就会信任数据库级联，而 SQLite 默认 foreign_keys=OFF，
    # 结果是删了会话消息还在。保留 ORM 级联，两条路径都安全。
    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.id",
    )


class Message(Base):
    """消息表（会话里的一轮发言）。"""

    __tablename__ = "messages"
    __table_args__ = (
        # role 只有三种取值，数据库层兜一道：防止 "User"/"assistant " 这类脏数据
        # 写进去之后，前端按 role 渲染直接崩
        CheckConstraint("role IN ('user', 'assistant', 'system')", name="role_allowed"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # tokens 默认 0：写入时还没算或算不出来就记 0，别用 NULL，
    # 否则 SUM(tokens) 统计时还得单独处理 NULL
    tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utc_now, nullable=False
    )

    conversation: Mapped["Conversation"] = relationship(back_populates="messages")


# ---------------------------------------------------------------------------
# Conversation.message_count：只读派生属性，不是数据库里的列
# ---------------------------------------------------------------------------
# 生成的 SQL 长这样（跟着会话一起查，不额外发请求）：
#   SELECT conversations.*,
#          (SELECT count(messages.id) FROM messages
#           WHERE messages.conversation_id = conversations.id) AS message_count
#   FROM conversations WHERE conversations.user_id = ?
#
# 为什么不写 @property + len(self.messages)：
# 那样列表页要么逐条触发 count 查询（N+1），要么用 selectinload 把这一批
# 会话的**全部消息正文**都读进内存 —— 一个会话几百条消息时就白搬了几 MB。
#
# 必须在 Message 定义之后赋值：子查询要引用 messages 表，
# 写在 class 体内时 Message 还不存在，会报 NoReferencedTableError。
Conversation.message_count = column_property(  # type: ignore[assignment]
    select(func.count(Message.id))
    .where(Message.conversation_id == Conversation.id)
    .correlate_except(Message)
    .scalar_subquery()
)
