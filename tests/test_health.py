"""健康检查用例（最便宜的一条冒烟测试）。

为什么值得留一个：它是唯一**不依赖数据库和鉴权**的接口。
CI 里如果这条红了，说明应用根本没起来（import 错、配置校验失败），
而不是业务逻辑的问题 —— 先看最上游，能省掉一轮瞎排查。
"""

from datetime import datetime

from fastapi.testclient import TestClient


def test_health_returns_ok_and_timezone_aware_timestamp(client: TestClient) -> None:
    """防回归：health 时间戳丢时区（naive datetime）。

    naive 时间序列化出来没有 Z 后缀，前端按本地时区解释直接差 8 小时，
    而且只在非 UTC 机器上才看得出来。这里断言解析后 tzinfo 不为 None。
    """
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"]

    # fromisoformat 能直接吃 "2026-10-29T11:45:00+00:00" 与 "...Z"（3.11+）
    timestamp = datetime.fromisoformat(body["timestamp"].replace("Z", "+00:00"))
    assert timestamp.tzinfo is not None
