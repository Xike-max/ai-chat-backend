"""会话相关路由（知识点 3、4、5、7）。

本版没有数据库，数据全部来自模块级假数据，目的是把路由、参数校验、分页
与响应模型跑通；字段结构与后续 SQLAlchemy 模型保持一致。

路由顺序提醒：`/latest` 必须声明在 `/{conversation_id}` 之前。
路由是按声明顺序匹配的，若 `/{conversation_id}` 在前，请求 `/latest`
会先命中该路径模板，再去把 "latest" 转 int，结果就是 422 而不是 200。
"""

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Response, status

from app.deps import Pagination
from app.errors import NotFoundError
from app.schemas import ConversationCreate
from app.schemas_response import ConversationDetail, ConversationOut, MessageOut, Page

router = APIRouter(prefix="/conversations", tags=["会话"])


# ---------------- 假数据 ----------------
# TODO: 知识点 9 换成数据库（改由 crud 层按 user_id 查询 conversations / messages 表）
FAKE_CONVERSATIONS: list[dict] = [
    {
        "id": 1,
        "title": "FastAPI 路由顺序怎么排",
        "model": "deepseek-chat",
        "archived": False,
        "summary": "讨论了静态路径与动态路径的匹配优先级。",
        "created_at": "2026-10-20T09:12:00Z",
        "updated_at": "2026-10-24T18:30:00Z",
        "messages": [
            {
                "id": 1,
                "role": "user",
                "content": "为什么 /latest 会被 /{conversation_id} 吃掉？",
                "created_at": "2026-10-20T09:12:00Z",
            },
            {
                "id": 2,
                "role": "assistant",
                "content": "因为路由按声明顺序匹配，静态路径要写在动态路径之前。",
                "created_at": "2026-10-20T09:12:20Z",
            },
        ],
    },
    {
        "id": 2,
        "title": "把模型白名单做成 Enum",
        "model": "qwen-plus",
        "archived": False,
        "created_at": "2026-10-25T14:00:00Z",
        "updated_at": "2026-10-27T09:45:00Z",
        "messages": [
            {
                "id": 3,
                "role": "user",
                "content": "模型名用 Enum 有什么好处？",
                "created_at": "2026-10-25T14:00:00Z",
            },
            {
                "id": 4,
                "role": "assistant",
                "content": "非法值由 FastAPI 直接 422，且 /docs 会渲染成下拉框。",
                "created_at": "2026-10-25T14:00:15Z",
            },
        ],
    },
    {
        "id": 3,
        "title": "分页参数怎么设计才不被前端骂",
        "model": "deepseek-chat",
        "archived": False,
        "summary": "total 必须是筛选后、分页前的数量。",
        "created_at": "2026-10-21T10:00:00Z",
        "updated_at": "2026-10-26T16:20:00Z",
        "messages": [
            {
                "id": 5,
                "role": "user",
                "content": "total 应该给筛选前的还是筛选后的？",
                "created_at": "2026-10-21T10:00:00Z",
            },
            {
                "id": 6,
                "role": "assistant",
                "content": "给筛选后、分页前的总数，前端才能算对总页数。",
                "created_at": "2026-10-21T10:00:12Z",
            },
        ],
    },
    {
        "id": 4,
        "title": "老项目迁移记录（已归档）",
        "model": "gpt-4o-mini",
        "archived": True,
        "created_at": "2026-09-10T08:00:00Z",
        "updated_at": "2026-09-30T20:00:00Z",
        "messages": [],
    },
    {
        "id": 5,
        "title": "Streamlit 前端联调问题",
        "model": "qwen-plus",
        "archived": False,
        "created_at": "2026-10-23T11:30:00Z",
        "updated_at": "2026-10-25T09:05:00Z",
        "messages": [
            {
                "id": 7,
                "role": "user",
                "content": "CORS 报错怎么排查？",
                "created_at": "2026-10-23T11:30:00Z",
            }
        ],
    },
    {
        "id": 6,
        "title": "临时测试会话（已归档）",
        "model": "gpt-4o-mini",
        "archived": True,
        "created_at": "2026-08-01T08:00:00Z",
        "updated_at": "2026-08-02T08:00:00Z",
        "messages": [],
    },
]

# 再补一批只有摘要字段的会话，让「total 与当前页条数不同」这件事能被真正验证
# （默认 limit=20，未归档总数 22 → 第一页 20 条、total=22）
for _index in range(7, 31):
    FAKE_CONVERSATIONS.append(
        {
            "id": _index,
            "title": f"历史会话 {_index:02d}",
            "model": "deepseek-chat",
            "archived": _index % 4 == 0,
            "created_at": "2026-09-01T09:00:00Z",
            "updated_at": f"2026-09-{_index - 6:02d}T09:00:00Z",
            "messages": [],
        }
    )
del _index


def _to_out(conversation: dict) -> ConversationOut:
    """内部字典 → 对外摘要模型。

    Args:
        conversation: 一条会话假数据（含 hashed 类内部字段也无需担心，
            因为 ConversationOut 只声明了要暴露的列）。

    Returns:
        ConversationOut: 只含 id/title/created_at/message_count/summary。
    """
    return ConversationOut(
        id=conversation["id"],
        title=conversation["title"],
        created_at=conversation["created_at"],
        message_count=len(conversation["messages"]),
        summary=conversation.get("summary"),
    )


def _to_detail(conversation: dict) -> ConversationDetail:
    """内部字典 → 对外详情模型（摘要 + 全部消息）。

    Args:
        conversation: 一条会话假数据。

    Returns:
        ConversationDetail: 继承 ConversationOut，额外带 messages。
    """
    base = _to_out(conversation)
    return ConversationDetail(
        **base.model_dump(),
        messages=[MessageOut.model_validate(item) for item in conversation["messages"]],
    )


# ---------------- 接口 ----------------
@router.get(
    "",
    response_model=Page[ConversationOut],
    response_model_exclude_none=True,
    summary="获取会话列表（支持筛选与分页）",
)
async def list_conversations(
    pagination: Annotated[Pagination, Depends()],
    keyword: Annotated[
        str | None, Query(min_length=1, max_length=50, description="标题关键词，不区分大小写")
    ] = None,
    archived: Annotated[bool, Query(description="true 只看已归档，false 只看未归档")] = False,
) -> Page[ConversationOut]:
    """由前端侧边栏会话列表调用，返回筛选后的当前页数据与总条数。

    Args:
        pagination: 分页依赖，提供 skip / limit 及校验。
        keyword: 标题关键词，对 title 做不区分大小写的包含匹配。
        archived: 归档筛选，false（默认）只看未归档，true 只看已归档。

    Returns:
        Page[ConversationOut]: {"total": 筛选后总数, "items": 当前页会话摘要}。
    """
    rows = [item for item in FAKE_CONVERSATIONS if item["archived"] is archived]

    if keyword:
        # casefold 比 lower 对非 ASCII 更稳，中英文混排不会漏匹配
        needle = keyword.casefold()
        rows = [item for item in rows if needle in item["title"].casefold()]

    # 排序必须在分页之前：顺序不稳定会让翻页出现重复/漏条
    # TODO: 知识点 9 换成 order_by(Conversation.updated_at.desc(), Conversation.id.desc())
    rows.sort(key=lambda item: (item["updated_at"], item["id"]), reverse=True)

    # total 必须在分页之前统计，否则前端永远算不出总页数
    total = len(rows)

    # TODO: 知识点 9 换成 SQLAlchemy 的 offset/limit：
    #   select(Conversation).where(...).offset(pagination.skip).limit(pagination.limit)
    page_rows = pagination.paginate(rows)

    return Page[ConversationOut](total=total, items=[_to_out(item) for item in page_rows])


@router.post(
    "",
    response_model=ConversationOut,
    response_model_exclude_none=True,
    status_code=status.HTTP_201_CREATED,
    summary="新建会话",
)
async def create_conversation(payload: ConversationCreate) -> ConversationOut:
    """由前端「新建对话」按钮调用，创建一个会话并返回它的摘要。

    Args:
        payload: 标题与默认模型，两者都有默认值，可以只传 {}。

    Returns:
        ConversationOut: 新建的会话摘要；状态码 201，表示已创建资源。
    """
    # TODO: 知识点 9 换成数据库（insert + commit + refresh 拿自增 id）
    new_id = max((item["id"] for item in FAKE_CONVERSATIONS), default=0) + 1
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    conversation = {
        "id": new_id,
        "title": payload.title,
        "model": payload.model,
        "archived": False,
        "created_at": now,
        "updated_at": now,
        "messages": [],
    }
    FAKE_CONVERSATIONS.append(conversation)
    return _to_out(conversation)


# 注意：本路由必须写在 /{conversation_id} 之前，否则 latest 会被当成路径参数
@router.get(
    "/latest",
    response_model=ConversationOut,
    response_model_exclude_none=True,
    summary="获取最近活跃会话",
)
async def get_latest_conversation() -> ConversationOut:
    """由前端「继续上次对话」入口调用，返回 updated_at 最新的未归档会话摘要；没有时 404。"""
    candidates = [item for item in FAKE_CONVERSATIONS if not item["archived"]]
    if not candidates:
        # 用业务异常而不是 HTTPException：code 让前端能精确分支
        # （"还没建过会话"和"接口不存在"都是 404，但处理方式完全不同）
        raise NotFoundError(
            code="CONVERSATION_NOT_FOUND",
            message="当前没有任何会话，请先创建一个会话",
        )
    latest = max(candidates, key=lambda item: item["updated_at"])
    return _to_out(latest)


@router.get(
    "/{conversation_id}",
    response_model=ConversationDetail,
    response_model_exclude_none=True,
    summary="获取会话详情",
)
async def get_conversation(
    conversation_id: Annotated[int, Path(description="会话 ID", ge=1)],
) -> ConversationDetail:
    """由前端会话详情页调用，返回指定会话的摘要与全部消息；ID 不存在时 404。"""
    conversation = next(
        (item for item in FAKE_CONVERSATIONS if item["id"] == conversation_id), None
    )
    if conversation is None:
        raise NotFoundError(
            code="CONVERSATION_NOT_FOUND",
            message=f"会话 {conversation_id} 不存在，请确认 ID 是否正确",
        )
    return _to_detail(conversation)


@router.delete(
    "/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="删除会话",
)
async def delete_conversation(
    conversation_id: Annotated[int, Path(description="会话 ID", ge=1)],
) -> None:
    """由前端会话列表的「删除」操作调用，成功时返回 204。

    注意：204 No Content 按 HTTP 规范不允许携带响应体，
    所以这里 `return None` 而不是 `return {"ok": True}`。
    前端只能凭状态码判断成功；需要提示文案就自己在前端写。
    标注 `-> None` 也让 FastAPI 不生成 response_model，避免文档里画蛇添足。

    Args:
        conversation_id: 要删除的会话 ID。

    Raises:
        HTTPException: 404，会话不存在（幂等性交给调用方决定怎么处理）。
    """
    for index, item in enumerate(FAKE_CONVERSATIONS):
        if item["id"] == conversation_id:
            # TODO: 知识点 9 换成 db.delete(conversation) + db.commit()
            FAKE_CONVERSATIONS.pop(index)
            return None
    raise NotFoundError(
        code="CONVERSATION_NOT_FOUND",
        message=f"会话 {conversation_id} 不存在，无法删除",
    )
