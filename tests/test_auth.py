"""认证接口用例（注册 + 登录 + 用 token 访问受保护资源）。

为什么这些用例值得写：认证是整个系统唯一的入口，它坏掉的表现往往是
"线上所有人被踢下线"或"密码哈希被写进响应"，而这两件事都不会在
功能自测里被发现。
"""

from fastapi.testclient import TestClient

from tests.conftest import TEST_EMAIL, TEST_PASSWORD, TEST_USERNAME


def test_register_returns_201_and_never_leaks_password(client: TestClient) -> None:
    """防回归：注册响应里出现密码或哈希。

    一旦有人给 UserOut 加了 hashed_password 字段（或在路由里手拼 dict），
    密码哈希就会批量泄露到前端日志、浏览器缓存、APM 采样里 —— 而功能上
    一切正常，测试不盯着这一点就发现不了。
    """
    response = client.post(
        "/api/v1/auth/register",
        json={"username": TEST_USERNAME, "email": TEST_EMAIL, "password": TEST_PASSWORD},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["username"] == TEST_USERNAME
    # 邮箱统一小写入库（Alice@Example.com 与 alice@example.com 是同一个账号）
    assert body["email"] == TEST_EMAIL
    # 断言"关键字段存在"而不是整包相等：整包断言一改字段就红，维护成本高且没价值
    assert "hashed_password" not in body
    assert "password" not in body
    assert "$2b$" not in response.text  # bcrypt 哈希前缀，出现即泄露


def test_register_duplicate_username_returns_409(client: TestClient) -> None:
    """防回归：重复注册从 409 退化成 500。

    如果只靠数据库唯一约束而不捕获 IntegrityError，第二个同名注册会得到
    500（对前端来说就是"服务挂了"），而且注册按钮会被重复点击反复触发。
    """
    first = client.post(
        "/api/v1/auth/register",
        json={"username": TEST_USERNAME, "email": TEST_EMAIL, "password": TEST_PASSWORD},
    )
    assert first.status_code == 201

    duplicate = client.post(
        "/api/v1/auth/register",
        # 换个邮箱，确保冲突来自用户名这一列
        json={"username": TEST_USERNAME, "email": "other@example.com", "password": TEST_PASSWORD},
    )

    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "CONFLICT"


def test_login_returns_bearer_token_that_unlocks_me(client: TestClient) -> None:
    """防回归：token 签发成功但不可用（前缀、payload、鉴权链断裂）。

    "登录返回 200 但 /users/me 401"是最常见的联调事故：token_type 拼成
    "Token"、sub 用了 int、或者 oauth2_scheme 的 tokenUrl 写错。
    这条用例一次把「换 token → 带 token 取用户」整条链跑通。
    """
    client.post(
        "/api/v1/auth/register",
        json={"username": TEST_USERNAME, "email": TEST_EMAIL, "password": TEST_PASSWORD},
    )

    login = client.post(
        "/api/v1/auth/token",
        # 表单提交（OAuth2 密码模式）。改成 json= 会得到 422，这条断言守着它
        data={"username": TEST_USERNAME, "password": TEST_PASSWORD},
    )

    assert login.status_code == 200
    token_body = login.json()
    assert token_body["token_type"] == "bearer"
    assert token_body["access_token"]
    assert token_body["expires_in"] > 0

    me = client.get(
        "/api/v1/users/me",
        headers={"Authorization": f"Bearer {token_body['access_token']}"},
    )
    assert me.status_code == 200
    assert me.json()["username"] == TEST_USERNAME


def test_login_failure_is_401_and_does_not_reveal_which_part_is_wrong(
    client: TestClient,
) -> None:
    """防回归：登录失败提示被"优化"成"用户不存在"/"密码错误"两种。

    区分两者等于给攻击者一个账号探测器：先用常见用户名把库里的账号列出来，
    再集中撞库。这条用例要求两种失败**响应体逐字节相同**、且都带
    WWW-Authenticate，任何差异化提示都会让它变红。
    """
    client.post(
        "/api/v1/auth/register",
        json={"username": TEST_USERNAME, "email": TEST_EMAIL, "password": TEST_PASSWORD},
    )

    wrong_password = client.post(
        "/api/v1/auth/token",
        data={"username": TEST_USERNAME, "password": "wrong-password-123"},
    )
    unknown_user = client.post(
        "/api/v1/auth/token",
        data={"username": "nobody", "password": TEST_PASSWORD},
    )

    assert wrong_password.status_code == 401
    assert unknown_user.status_code == 401
    assert wrong_password.json() == unknown_user.json()
    assert wrong_password.json()["error"]["message"] == "用户名或密码错误"
    # 401 必须带认证方案，否则客户端的自动重新登录逻辑不触发
    assert wrong_password.headers["WWW-Authenticate"] == "Bearer"
