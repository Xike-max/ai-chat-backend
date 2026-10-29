"""会话接口用例（列表 / 新建 / 详情 / 删除 + 越权）。

这组用例的核心不是"接口能不能用"，而是**数据隔离**：这个项目所有会话都
存在同一张表里，唯一的分隔就是 `WHERE user_id = ?`。少写一次，就是一次
全量数据泄露，而功能测试（只用自己的账号跑）永远发现不了。
"""

from fastapi.testclient import TestClient

from tests.conftest import OTHER_EMAIL, OTHER_USERNAME, register_and_login


def _create_conversation(client: TestClient, headers: dict[str, str], title: str) -> int:
    """建一个会话并返回它的 id（供后续用例定位资源）。

    Args:
        client: 测试客户端。
        headers: 鉴权头。
        title: 会话标题。

    Returns:
        int: 新建会话的 id。
    """
    response = client.post("/api/v1/conversations", json={"title": title}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_create_conversation_returns_201_and_empty_message_count(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """防回归：新建会话返回 200 或 message_count 变成 null。

    201 是契约的一部分（前端据此判断"确实创建了"）；message_count 来自
    column_property，一旦有人把它改成 property + len(messages)，
    空会话会变成 None 或触发额外查询。
    """
    response = client.post(
        "/api/v1/conversations",
        json={"title": "RAG 讨论"},
        headers=auth_headers,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["title"] == "RAG 讨论"
    assert body["message_count"] == 0
    assert body["id"] > 0


def test_list_returns_only_my_conversations(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """防回归：会话列表漏掉 user_id 过滤（最严重的一类越权）。

    两个用户各自建会话，alice 的列表只应看到自己的两条。
    一旦有人把 crud.list_conversations 的 `Conversation.user_id == user_id`
    删掉，这里 total 会变成 3，用例立刻变红。
    """
    mine_a = _create_conversation(client, auth_headers, "我的第一条")
    mine_b = _create_conversation(client, auth_headers, "我的第二条")

    other_headers = register_and_login(
        client, username=OTHER_USERNAME, email=OTHER_EMAIL
    )
    _create_conversation(client, other_headers, "别人的会话")

    response = client.get("/api/v1/conversations", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert {item["id"] for item in body["items"]} == {mine_a, mine_b}


def test_get_detail_returns_empty_messages_for_new_conversation(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """防回归：详情接口的 messages 变成 500 或 null。

    新会话没有任何消息。这里既验响应结构（messages 是列表），也验
    "空列表而不是 null" —— 前端 `messages.map(...)` 遇到 null 会直接白屏。
    """
    conversation_id = _create_conversation(client, auth_headers, "空会话")

    response = client.get(f"/api/v1/conversations/{conversation_id}", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == conversation_id
    assert body["messages"] == []


def test_another_users_conversation_is_404_not_403(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """防回归：按 id 取会话时漏掉归属校验（**越权**）。

    这是本文件最重要的一条。把 crud.get_conversation 里的 user_id 条件删掉，
    bob 就能读到 alice 的会话，这里会从 404 变成 200 —— 用例必须红。

    顺带守 404 vs 403 的选择：403 等于承认"这个 id 存在，只是不是你的"，
    可以用来枚举别人的会话数量；404 什么都不透露。
    """
    alice_conversation = _create_conversation(client, auth_headers, "alice 的私密会话")

    bob_headers = register_and_login(client, username=OTHER_USERNAME, email=OTHER_EMAIL)
    response = client.get(
        f"/api/v1/conversations/{alice_conversation}", headers=bob_headers
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "CONVERSATION_NOT_FOUND"


def test_delete_own_conversation_returns_204_then_404(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """防回归：删除接口返回 200 带 body，或删完还能查到（事务没提交）。

    204 不允许带响应体，前端只认状态码；删完再查必须 404，
    否则说明 delete 只删了内存对象、没落库。
    """
    conversation_id = _create_conversation(client, auth_headers, "待删除")

    deleted = client.delete(f"/api/v1/conversations/{conversation_id}", headers=auth_headers)
    assert deleted.status_code == 204
    assert deleted.content == b""

    again = client.get(f"/api/v1/conversations/{conversation_id}", headers=auth_headers)
    assert again.status_code == 404
