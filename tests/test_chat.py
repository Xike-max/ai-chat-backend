"""对话接口用例（调用模型 / 落库 / 越权 / 参数校验 / 上游故障）。

这个接口同时碰三样最容易出事的东西：鉴权、数据库写入、外部服务。
测试要证明的是：**它在外部服务不可用、以及别人来敲门时，行为也正确**。
"""

from fastapi.testclient import TestClient

from app.llm import LLMError
from tests.conftest import (
    FAKE_REPLY,
    OTHER_EMAIL,
    OTHER_USERNAME,
    FakeLLMClient,
    register_and_login,
)


def _create_conversation(client: TestClient, headers: dict[str, str], title: str) -> int:
    """建一个会话并返回 id。

    Args:
        client: 测试客户端。
        headers: 鉴权头。
        title: 会话标题。

    Returns:
        int: 会话 id。
    """
    response = client.post("/api/v1/conversations", json={"title": title}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_chat_returns_reply_and_persists_both_messages(
    client: TestClient, auth_headers: dict[str, str], mock_llm: FakeLLMClient
) -> None:
    """防回归：回复只返回不落库，或 assistant 消息没存。

    用户刷新页面后聊天记录少一条，是最典型的"看起来能用、实际丢数据"。
    这条用例要求：响应里的 reply 来自 LLM 层（等于假客户端文本），
    且详情接口能查到 user + assistant 两条消息。
    """
    conversation_id = _create_conversation(client, auth_headers, "对话测试")

    response = client.post(
        f"/api/v1/conversations/{conversation_id}/chat",
        json={"message": "什么是向量数据库？"},
        headers=auth_headers,
    )

    assert response.status_code == 200
    body = response.json()
    # 断言它等于假客户端的文本，才能证明回复真的走了 LLM 层，
    # 而不是路由里自己拼了一句
    assert body["reply"] == FAKE_REPLY
    assert body["conversation_id"] == conversation_id
    assert body["message"]["role"] == "assistant"
    assert body["message"]["content"] == FAKE_REPLY

    # 假客户端只被调用一次：并发/重试逻辑写错时会变成 2 次以上
    assert len(mock_llm.calls) == 1

    detail = client.get(f"/api/v1/conversations/{conversation_id}", headers=auth_headers)
    messages = detail.json()["messages"]
    assert [item["role"] for item in messages] == ["user", "assistant"]
    assert messages[0]["content"] == "什么是向量数据库？"
    assert messages[1]["content"] == FAKE_REPLY


def test_chat_payload_carries_history_and_options(
    client: TestClient, auth_headers: dict[str, str], mock_llm: FakeLLMClient
) -> None:
    """防回归：历史消息没拼进 payload（模型变成"失忆"）。

    每轮只把当前这句话发给模型，用户会觉得"它忘了刚才说的一切"。
    这里断言第二次调用时 payload 的 messages 是 user/assistant/user 三条，
    并且采样参数被原样透传。
    """
    conversation_id = _create_conversation(client, auth_headers, "多轮对话")

    client.post(
        f"/api/v1/conversations/{conversation_id}/chat",
        json={"message": "第一句"},
        headers=auth_headers,
    )
    client.post(
        f"/api/v1/conversations/{conversation_id}/chat",
        json={"message": "第二句", "options": {"temperature": 0.2, "max_tokens": 64}},
        headers=auth_headers,
    )

    assert len(mock_llm.calls) == 2
    payload = mock_llm.calls[1]
    assert [item["content"] for item in payload["messages"]] == [
        "第一句",
        FAKE_REPLY,
        "第二句",
    ]
    assert payload["temperature"] == 0.2
    assert payload["max_tokens"] == 64


def test_chat_to_another_users_conversation_is_404(
    client: TestClient, auth_headers: dict[str, str], mock_llm: FakeLLMClient
) -> None:
    """防回归：对话接口漏掉归属校验（往别人的会话里写消息）。

    比"读"更严重：不仅能读走别人的上下文，还会把内容写进别人的聊天记录。
    把 crud.get_conversation 的 user_id 条件删掉，这里会变成 200。
    """
    alice_conversation = _create_conversation(client, auth_headers, "alice 的会话")

    bob_headers = register_and_login(client, username=OTHER_USERNAME, email=OTHER_EMAIL)
    response = client.post(
        f"/api/v1/conversations/{alice_conversation}/chat",
        json={"message": "我想插一句"},
        headers=bob_headers,
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "CONVERSATION_NOT_FOUND"
    # 关键：没通过鉴权的请求**不能**触发模型调用（否则等于把别人的数据算力花了）
    assert mock_llm.calls == []


def test_chat_with_empty_message_returns_422_with_field_path(
    client: TestClient, auth_headers: dict[str, str], mock_llm: FakeLLMClient
) -> None:
    """防回归：空消息被放行到模型（浪费 token 且模型会答非所问）。

    同时守住 422 的字段路径格式：前端要按 `body.message` 精确标红，
    一旦退回成嵌套 loc 数组，前端就得为这个接口单独写解析。
    """
    conversation_id = _create_conversation(client, auth_headers, "校验测试")

    response = client.post(
        f"/api/v1/conversations/{conversation_id}/chat",
        json={"message": ""},
        headers=auth_headers,
    )

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    # 字段路径必须是点分形式（body.message），前端才能精确标红；
    # 不断言具体文案：空字符串触发的是 string_too_short（长度不足），
    # 而"缺字段"才是 missing（必填）—— 把中文文案写死在用例里，
    # 每次产品改文案都要改测试，那种测试很快就会被随手改成 assert True
    assert error["detail"][0]["field"] == "body.message"
    assert error["detail"][0]["message"]
    assert mock_llm.calls == []


def test_chat_when_llm_is_down_returns_502_and_keeps_user_message(
    client: TestClient, auth_headers: dict[str, str], mock_llm: FakeLLMClient
) -> None:
    """防回归：上游模型故障被吞成 200（空回复）或被抛成 500（带栈）。

    502 让前端知道"是模型侧的问题，可以重试"；500 会让人以为是我们的 bug。
    同时要求用户消息已经落库：模型挂了不该把用户刚打的那段话也弄丢。
    """
    conversation_id = _create_conversation(client, auth_headers, "故障测试")
    mock_llm.error = LLMError("upstream timeout")

    response = client.post(
        f"/api/v1/conversations/{conversation_id}/chat",
        json={"message": "这句话必须留下来"},
        headers=auth_headers,
    )

    assert response.status_code == 502
    error = response.json()["error"]
    assert error["code"] == "LLM_UNAVAILABLE"
    # 响应里不能出现上游细节（异常文本、URL、密钥前缀）
    assert "upstream timeout" not in response.text

    detail = client.get(f"/api/v1/conversations/{conversation_id}", headers=auth_headers)
    messages = detail.json()["messages"]
    assert [item["role"] for item in messages] == ["user"]
    assert messages[0]["content"] == "这句话必须留下来"
