"""会话相关路由（知识点 3、4、5、7、9、12）。

这一版把原来的模块级假数据换成了数据库 + 鉴权，是测试能真正验证
"越权"和"数据隔离"的前提：假数据既没有归属，也没有持久化，
用例无论怎么写都验不出"别人的会话读不到"。

三条贯穿全文件的规矩：

1. **每个接口都要 `CurrentUser`**。少写一个，就等于给这个资源留了一个
   匿名入口，而它通常不会在功能测试里暴露。
2. **按 id 取数据必须同时带 user_id**（见 crud.get_conversation）。
   主键不是访问凭证：自增 id 从 1 开始，攻击者 +1 就能遍历。
3. **归属不匹配时返回 404，不是 403**。403 等于承认"这个 id 存在，
   只是不是你的"，可以用来探测别人有多少会话；404 什么都不透露。

路由顺序提醒：`/latest` 必须声明在 `/{conversation_id}` 之前。
路由是按声明顺序匹配的，若 `/{conversation_id}` 在前，请求 `/latest`
会先命中该路径模板，再去把 "latest" 转 int，结果就是 422 而不是 200。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Response, status
from sqlalchemy.orm import Session

from app import crud
from app.deps import CurrentUser, Pagination, get_db
from app.errors import NotFoundError
from app.schemas import (
    ConversationCreate,
    ConversationDetail,
    ConversationOut,
    Page,
)

router = APIRouter(prefix="/conversations", tags=["会话"])

# 详情页一次返回的最大消息数。聊天记录可能很长，全量返回会把响应撑到几 MB，
# 前端首屏也会卡；超出部分由调用方翻页（后续可加 /conversations/{id}/messages）。
_DETAIL_MESSAGE_LIMIT = 50


def _not_found(conversation_id: int) -> NotFoundError:
    """构造"会话不存在"的 404 业务异常（文案统一，不区分不存在/不属于你）。

    Args:
        conversation_id: 路径里的会话 id。

    Returns:
        NotFoundError: code 固定为 CONVERSATION_NOT_FOUND。
    """
    return NotFoundError(
        code="CONVERSATION_NOT_FOUND",
        message=f"会话 {conversation_id} 不存在，请确认 ID 是否正确",
    )


@router.get(
    "",
    response_model=Page[ConversationOut],
    response_model_exclude_none=True,
    summary="获取会话列表（支持筛选与分页）",
)
def list_conversations(
    pagination: Annotated[Pagination, Depends()],
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    keyword: Annotated[
        str | None, Query(min_length=1, max_length=50, description="标题关键词，不区分大小写")
    ] = None,
    archived: Annotated[bool, Query(description="true 只看已归档，false 只看未归档")] = False,
) -> Page[ConversationOut]:
    """由前端侧边栏会话列表调用，返回当前用户筛选后的当前页数据与总条数。

    Args:
        pagination: 分页依赖，提供 skip / limit 及校验。
        user: 当前登录用户（由 Bearer token 解析并回查数据库）。
        db: 数据库会话。
        keyword: 标题关键词，对 title 做不区分大小写的包含匹配。
        archived: 归档筛选，false（默认）只看未归档，true 只看已归档。

    Returns:
        Page[ConversationOut]: {"total": 筛选后总数, "items": 当前页会话摘要}。
    """
    rows, total = crud.list_conversations(
        db,
        user_id=user.id,
        skip=pagination.skip,
        limit=pagination.limit,
        keyword=keyword,
        archived=archived,
    )
    # 直接返回 ORM 对象列表：ConversationOut 开了 from_attributes，
    # 且 message_count 是 column_property（跟着同一条 SQL 查出来，不是 N+1）
    return Page[ConversationOut](total=total, items=rows)


@router.post(
    "",
    response_model=ConversationOut,
    response_model_exclude_none=True,
    status_code=status.HTTP_201_CREATED,
    summary="新建会话",
)
def create_conversation(
    payload: ConversationCreate,
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
) -> ConversationOut:
    """由前端「新建对话」按钮调用，创建一个会话并返回它的摘要。

    Args:
        payload: 标题与默认模型，两者都有默认值，可以只传 {}。
        user: 当前登录用户；``user_id`` **只能**从这里取，不能信请求体。
        db: 数据库会话。

    Returns:
        ConversationOut: 新建的会话摘要；状态码 201，表示已创建资源。

    Note:
        ``payload.model`` 目前没有落库（conversations 表还没有 model 列）。
        这不是"忘了"，而是刻意不让这一节顺手改表结构：加列要走迁移。
        该字段继续被接收，是为了前端契约不变；等表结构补上再真正存。
    """
    conversation = crud.create_conversation(
        db,
        user_id=user.id,
        title=payload.title,
    )
    return conversation


# 注意：本路由必须写在 /{conversation_id} 之前，否则 latest 会被当成路径参数
@router.get(
    "/latest",
    response_model=ConversationOut,
    response_model_exclude_none=True,
    summary="获取最近活跃会话",
)
def get_latest_conversation(
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
) -> ConversationOut:
    """由前端「继续上次对话」入口调用，返回当前用户最近更新的未归档会话；没有时 404。

    Args:
        user: 当前登录用户。
        db: 数据库会话。

    Returns:
        ConversationOut: updated_at 最新的未归档会话摘要。

    Raises:
        NotFoundError: 404，该用户还没有任何未归档会话。
    """
    # 复用列表查询（已按 updated_at DESC, id DESC 排序），只取第一条：
    # 排序逻辑只有一处，避免"列表页的顺序"和"继续对话挑的那条"不一致
    rows, _ = crud.list_conversations(db, user_id=user.id, skip=0, limit=1)
    if not rows:
        raise NotFoundError(
            code="CONVERSATION_NOT_FOUND",
            message="当前没有任何会话，请先创建一个会话",
        )
    return rows[0]


@router.get(
    "/{conversation_id}",
    response_model=ConversationDetail,
    response_model_exclude_none=True,
    summary="获取会话详情",
)
def get_conversation(
    conversation_id: Annotated[int, Path(description="会话 ID", ge=1)],
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
) -> ConversationDetail:
    """由前端会话详情页调用，返回指定会话的摘要与最近的若干条消息。

    Args:
        conversation_id: 路径里的会话 id。
        user: 当前登录用户；归属校验在 crud.get_conversation 的 WHERE 里完成。
        db: 数据库会话。

    Returns:
        ConversationDetail: 会话摘要 + 按时间正序的消息列表。

    Raises:
        NotFoundError: 404，会话不存在**或**不属于当前用户（两种情况同一响应）。
    """
    conversation = crud.get_conversation(db, conversation_id, user.id)
    if conversation is None:
        raise _not_found(conversation_id)

    messages, _ = crud.list_messages(db, conversation_id, skip=0, limit=_DETAIL_MESSAGE_LIMIT)
    return ConversationDetail(
        id=conversation.id,
        title=conversation.title,
        created_at=conversation.created_at,
        message_count=conversation.message_count,
        summary=conversation.summary,
        messages=messages,
    )


@router.delete(
    "/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="删除会话",
)
def delete_conversation(
    conversation_id: Annotated[int, Path(description="会话 ID", ge=1)],
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
) -> None:
    """由前端会话列表的「删除」操作调用，成功时返回 204。

    注意：204 No Content 按 HTTP 规范不允许携带响应体，
    所以这里 `return None` 而不是 `return {"ok": True}`。
    前端只能凭状态码判断成功；需要提示文案就自己在前端写。
    标注 `-> None` 也让 FastAPI 不生成 response_model，避免文档里画蛇添足。

    Args:
        conversation_id: 要删除的会话 ID。
        user: 当前登录用户。
        db: 数据库会话。

    Raises:
        NotFoundError: 404，会话不存在或不属于当前用户（幂等性交给调用方决定）。
    """
    # 归属校验在 crud.delete_conversation 内部：它先按 id + user_id 查，
    # 查不到直接返回 False。**不要**改成 db.get(Conversation, id) 再比 user_id：
    # 那种写法一旦漏掉比较，任何人都能删别人的会话。
    if not crud.delete_conversation(db, conversation_id, user.id):
        raise NotFoundError(
            code="CONVERSATION_NOT_FOUND",
            message=f"会话 {conversation_id} 不存在，无法删除",
        )
    return None
