"""内部接口调用示例：先 `uvicorn app.main:app`，再 `python scripts/call_internal.py`。"""
import os

import httpx
BASE = os.getenv("BASE_URL", "http://127.0.0.1:8000")
HEAD = {"X-API-Key": os.getenv("API_KEY", "dev-api-key-change-me")}

# trust_env=False：本机若设了 HTTP_PROXY/HTTPS_PROXY，httpx 默认会走代理，
# 代理把请求行改成「GET http://127.0.0.1:8000/... 」这种绝对形式，Starlette 认不出路径直接 404。
# 调本机/内网服务一律绕开代理，也顺手避免凭证被中间代理看到。
with httpx.Client(base_url=BASE, timeout=5, trust_env=False) as c:
    print("① 正常 deep health :", c.get("/internal/health/deep", headers=HEAD).status_code)
    r = c.post("/internal/usage/report", headers=HEAD,
               json={"date": "2026-10-27", "total_tokens": 128400, "request_count": 512})
    print("② 正常 usage report:", r.status_code, r.json())
    for tag, resp in (("③ 不带 key", c.get("/internal/health/deep")),
                      ("④ 错误 key", c.get("/internal/health/deep", headers={"X-API-Key": "wrong-key"})),
                      ("⑤ key 放 query", c.get("/internal/health/deep", params={"api_key": HEAD["X-API-Key"]}))):
        print(f"{tag:16} {resp.status_code} | {resp.json()['detail']}")
    print("   WWW-Authenticate =", c.get("/internal/health/deep").headers.get("www-authenticate"))
