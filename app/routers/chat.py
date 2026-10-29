"""会话内对话路由（知识点 15 的演示目标）。

一个接口：`POST /api/v1/conversations/{conversation_id}/chat`

执行顺序（顺序本身就是安全边界）：

    1. 鉴权          —— CurrentUser：token 无效/账号被禁用直接 401
    2. 归属校验      —— crud.get_conversation(db, id, user.id)：不是自己的会话 → 404
    3. 取历史消息    —— 只取该会话最近 20 条，拼进 payload
    4. 落库用户消息  —— 先写 user，再调模型：即使模型超时，用户的原话也没丢
    5. 调 LLM        —— 通过 get_llm_client 依赖（测试里被换成假客户端）
    6. 落库回复      —— assistant 消息
    7. 返回          —— reply + 落库后的消息（带 id / created_at）

两个刻意的选择：

- 本函数是 `def` 而不是 `async def`：底下的 `LLMClient.chat` 是同步阻塞调用，
  写在 async 里会卡死事件循环（一次调用能卡 30 秒，整个进程一起等）。
  `def` 会被丢进线程池，代价是一个线程，换来并发不互相拖死。
- 第 4 步先落库再调模型：如果先调模型再一起提交，模型失败时用户的输入就
  凭空消失了，重试还得重新打一遍字。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path
from sqlalchemy.orm import Session

from app import crud
from app.deps import CurrentUser, get_db
from app.errors import AppError, NotFoundError
from app.llm import LLMClient, LLMError, get_llm_client
from app.schemas import ChatIn, ChatOut, ChatRequest, MessageIn, MessageOut

router = APIRouter(prefix="/conversations", tags=["对话"])

# 拼进 payload 的历史消息条数上限。
# 上限必须存在：上下文是有限资源，无脑全量拼进去，长会话迟早顶爆模型上下文，
# 那时报的是上游的 context_length_exceeded，排查成本极高。
_HISTORY_LIMIT = 20


@router.post(
    "/{conversation_id}/chat",
    response_model=ChatOut,
    summary="在会话内继续对话",
    responses={
        404: {"description": "会话不存在或不属于当前用户"},
        422: {"description": "message 为空或超长"},
        502: {"description": "上游模型服务不可用"},
    },
)
def chat(
    payload: ChatIn,
    conversation_id: Annotated[int, Path(description="会话 ID", ge=1)],
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    llm: Annotated[LLMClient, Depends(get_llm_client)],
) -> ChatOut:
    """把用户这句话发给模型，保存一来一回两条消息，返回模型回复。

    Args:
        payload: 本轮输入（message / model / options）。
        conversation_id: 目标会话 id。
        user: 当前登录用户；归属校验用的是它的 id。
        db: 数据库会话。
        llm: LLM 客户端依赖；测试里由 `mock_llm` 夹具替换成假客户端，
            保证**不会**发出真实网络请求。

    Returns:
        ChatOut: conversation_id、reply、model 与已落库的 assistant 消息。

    Raises:
        NotFoundError: 404，会话不存在或不属于当前用户。
        AppError: 502，上游模型服务不可用（LLM_UNAVAILABLE）。
    """
    # 第 2 步：id + user_id 写在同一个 WHERE 里。少了 user_id 这一半，
    # 任何登录用户都能往别人的会话里写消息、读别人的上下文。
    conversation = crud.get_conversation(db, conversation_id, user.id)
    if conversation is None:
        raise NotFoundError(
            code="CONVERSATION_NOT_FOUND",
            message=f"会话 {conversation_id} 不存在，请确认 ID 是否正确",
        )

    # 第 3 步：历史消息按 id 正序，转成 MessageIn 由 to_llm_payload 拼装。
    # 只需要 role / content，其余字段（tokens 等）不进 payload。
    history_rows, _ = crud.list_messages(db, conversation_id, skip=0, limit=_HISTORY_LIMIT)
    history = [
        MessageIn(role=row.role, content=row.content)
        for row in history_rows
        # system 消息不回灌：它每轮都会由 system_prompt 重新给出，
        # 混进历史里出现两遍，模型会开始重复"你是一个..."这类设定
        if row.role in {"user", "assistant"}
    ]

    # 第 4 步：先落库用户消息。tokens 记 0（这里没有分词器，别猜一个假数字）
    crud.add_message(db, conversation_id, role="user", content=payload.message)

    # 复用 ChatRequest.to_llm_payload：payload 的拼装规则只写一份，
    # 免得"这里加了 system_prompt，那里忘了"。
    request = ChatRequest(
        message=payload.message,
        model=payload.model,
        conversation_id=conversation_id,
        options=payload.options,
    )

    # 第 5 步：调模型。任何上游异常都翻译成 502 —— 500 会让前端以为是我们挂了，
    # 502 明确表达"下游不可用"，也便于告警按 code 分流。
    try:
        reply = llm.chat(request.to_llm_payload(history))
    except LLMError as exc:
        # 不把 str(exc) 放进 message：里面可能有上游 URL、密钥前缀、对端报文
        raise AppError(
            "模型服务暂时不可用，请稍后重试",
            code="LLM_UNAVAILABLE",
            status_code=502,
        ) from exc

    # 第 6 步：落库助手回复，同时把会话的 updated_at 顶新（crud.add_message 内做）
    message = crud.add_message(db, conversation_id, role="assistant", content=reply)

    # 第 7 步：message 是 ORM 对象，MessageOut 的 from_attributes 直接吃
    return ChatOut(
        conversation_id=conversation_id,
        reply=reply,
        model=payload.model,
        message=MessageOut.model_validate(message),
    )
