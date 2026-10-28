"""数据访问层（知识点 9）。

约定：
- 每个函数第一个参数都是 `db: Session`，由路由的 `Depends(get_db)` 注入；
- 这一层只做 SQL 与 ORM 操作，不抛 HTTPException（状态码属于路由的职责），
  查不到就返回 None / False，由路由决定翻译成 404 还是 403；
- **所有按 id 取数据的函数都必须带 user_id**。主键不是访问凭证：
  `WHERE id = ?` 会直接把别人的会话返回出去，这是 SaaS 最常见的一类越权。
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Conversation, Message, utc_now


# SQL 等价语句：
#   INSERT INTO conversations (user_id, title, system_prompt, archived, created_at, updated_at)
#   VALUES (?, ?, ?, 0, ?, ?);
#   SELECT * FROM conversations WHERE id = ?;   -- refresh 取回自增 id 与默认值
def create_conversation(
    db: Session,
    user_id: int,
    title: str,
    system_prompt: str | None = None,
) -> Conversation:
    """新建会话并落库，返回带自增 id 的 ORM 对象。

    Args:
        db: 数据库会话。
        user_id: 会话归属用户；由鉴权层解析得到，**不能**从请求体里取。
        title: 会话标题。
        system_prompt: 系统提示词，可为空。

    Returns:
        Conversation: 已提交的会话对象，id / created_at / updated_at 均已填充。
    """
    conversation = Conversation(
        user_id=user_id,
        title=title,
        system_prompt=system_prompt,
        archived=False,
    )
    db.add(conversation)
    db.commit()
    # refresh 会回读一次，确保拿到数据库端生成的默认值（如 server_default）
    db.refresh(conversation)
    return conversation


# SQL 等价语句：
#   SELECT * FROM conversations WHERE id = ? AND user_id = ? LIMIT 1;
def get_conversation(db: Session, conversation_id: int, user_id: int) -> Conversation | None:
    """按 id + user_id 取会话，属于其他用户时返回 None。

    **防止越权：不能用 id 直接查**。
    `WHERE id = ?` 会把 id=1 的会话还给任何知道它存在的人 —— 自增 id 太好猜了，
    攻击者只要把路径里的数字 +1 就能读到别人的聊天记录。
    所以归属条件必须和主键写在同一个 WHERE 里，不能先查出来再在 Python 里
    比较 user_id：那样一旦某个分支忘了比较，就是数据泄露。

    Args:
        db: 数据库会话。
        conversation_id: 路径里的会话 id。
        user_id: 当前登录用户 id。

    Returns:
        Conversation | None: 归属当前用户时返回对象，否则 None（路由翻译成 404）。
    """
    stmt = select(Conversation).where(
        Conversation.id == conversation_id,
        Conversation.user_id == user_id,
    )
    return db.scalars(stmt).first()


# SQL 等价语句：
#   SELECT count(*) FROM conversations
#   WHERE user_id = ? AND archived = ? AND title LIKE '%关键字%';
#   SELECT * FROM conversations
#   WHERE user_id = ? AND archived = ? AND title LIKE '%关键字%'
#   ORDER BY updated_at DESC, id DESC LIMIT ? OFFSET ?;
def list_conversations(
    db: Session,
    user_id: int,
    skip: int = 0,
    limit: int = 20,
    keyword: str | None = None,
    archived: bool = False,
) -> tuple[list[Conversation], int]:
    """分页查询某用户的会话列表。

    Args:
        db: 数据库会话。
        user_id: 当前登录用户 id，查询条件里必须有。
        skip: 跳过条数（offset）。
        limit: 每页条数。
        keyword: 标题关键词，不区分大小写。
        archived: True 只看已归档，False 只看未归档。

    Returns:
        tuple[list[Conversation], int]: (当前页会话, 筛选后分页前的总数)。
    """
    filters = [Conversation.user_id == user_id, Conversation.archived == archived]
    if keyword:
        # ilike 在 PostgreSQL 上是 ILIKE，SQLite 上退化成 LIKE（ASCII 不区分大小写，
        # 中文本来也没有大小写）；关键字里的 % _ 由调用方保证已转义
        filters.append(Conversation.title.ilike(f"%{keyword}%"))

    total = db.scalar(select(func.count()).select_from(Conversation).where(*filters)) or 0

    stmt = (
        select(Conversation)
        .where(*filters)
        # 排序必须稳定：只按 updated_at 排，同一秒内的多条会话顺序会抖动，
        # 翻页时就会出现"第 1 页和第 2 页都有 id=5"这种重复
        .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
        .offset(skip)
        .limit(limit)
    )
    return list(db.scalars(stmt).all()), total


# SQL 等价语句：
#   SELECT * FROM conversations WHERE id = ? AND user_id = ?;
#   DELETE FROM messages WHERE conversation_id = ?;   -- 由 ORM cascade 发出
#   DELETE FROM conversations WHERE id = ? AND user_id = ?;
def delete_conversation(db: Session, conversation_id: int, user_id: int) -> bool:
    """删除属于当前用户的会话及其全部消息。

    **防止越权：不能用 id 直接查**，理由同 get_conversation —— 否则任何人
    都能删掉别人的会话。这里额外注意：即使 id 存在但 user_id 不匹配，
    也一律返回 False（路由给 404，而不是 403），避免用"存在/不存在"的差异
    探测出别人有哪些会话。

    Args:
        db: 数据库会话。
        conversation_id: 要删除的会话 id。
        user_id: 当前登录用户 id。

    Returns:
        bool: 删除成功 True；会话不存在或不属于该用户 False。
    """
    conversation = get_conversation(db, conversation_id, user_id)
    if conversation is None:
        return False

    # db.delete 会按 cascade="all, delete-orphan" 把 messages 一并删掉，
    # 不需要手写 DELETE FROM messages
    db.delete(conversation)
    db.commit()
    return True


# SQL 等价语句：
#   INSERT INTO messages (conversation_id, role, content, tokens, created_at)
#   VALUES (?, ?, ?, ?, ?);
#   UPDATE conversations SET updated_at = ? WHERE id = ?;
def add_message(
    db: Session,
    conversation_id: int,
    role: str,
    content: str,
    tokens: int = 0,
) -> Message:
    """向会话追加一条消息，并刷新会话的 updated_at。

    安全提示：本函数只按 conversation_id 写入，**不校验归属**。
    调用前必须先用 get_conversation(db, conversation_id, user_id) 确认这个
    会话属于当前用户，否则等于允许往别人的会话里插消息。

    Args:
        db: 数据库会话。
        conversation_id: 目标会话 id（调用方已校验过归属）。
        role: user / assistant / system，数据库有 CHECK 约束兜底。
        content: 消息正文。
        tokens: 本条消息消耗的 token 数，未知时记 0。

    Returns:
        Message: 已提交的消息对象。
    """
    message = Message(
        conversation_id=conversation_id,
        role=role,
        content=content,
        tokens=tokens,
    )
    db.add(message)

    # 会话列表按 updated_at 排序，新消息进来必须把会话顶到最前面。
    # 用 db.get 拿对象（本地已缓存的不会发 SELECT），改动会在同一次 commit 里落库
    conversation = db.get(Conversation, conversation_id)
    if conversation is not None:
        # onupdate=utc_now 只在列被实际修改时触发，这里显式赋值更直观
        conversation.updated_at = utc_now()

    db.commit()
    db.refresh(message)
    return message


# SQL 等价语句：
#   SELECT count(*) FROM messages WHERE conversation_id = ?;
#   SELECT * FROM messages WHERE conversation_id = ?
#   ORDER BY id ASC LIMIT ? OFFSET ?;
def list_messages(
    db: Session,
    conversation_id: int,
    skip: int = 0,
    limit: int = 50,
) -> tuple[list[Message], int]:
    """按时间正序分页查询某会话的消息。

    安全提示：同样需要调用方先确认会话归属（用 get_conversation），
    否则会变成"知道 conversation_id 就能读别人聊天记录"。

    Args:
        db: 数据库会话。
        conversation_id: 会话 id（调用方已校验过归属）。
        skip: 跳过条数。
        limit: 每页条数。

    Returns:
        tuple[list[Message], int]: (当前页消息, 该会话消息总数)。
    """
    total = (
        db.scalar(
            select(func.count())
            .select_from(Message)
            .where(Message.conversation_id == conversation_id)
        )
        or 0
    )

    stmt = (
        select(Message)
        .where(Message.conversation_id == conversation_id)
        # 按 id 正序：聊天记录必须从旧到新，且 id 比 created_at 更稳
        # （同一秒写入的多条消息 time 完全相同）
        .order_by(Message.id.asc())
        .offset(skip)
        .limit(limit)
    )
    return list(db.scalars(stmt).all()), total
