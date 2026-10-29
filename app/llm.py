"""LLM 调用封装：对外只暴露 `LLMClient` 与 `get_llm_client`。

为什么单独一个模块、并且做成"依赖"而不是一个函数调用：

1. **路由不该知道 HTTP 的存在**。路由只该说"给我一段回复"，
   超时、重试、非 2xx、返回体结构这些脏活全部收在这里；
2. **测试要能一键替换**。`get_llm_client` 是一个 FastAPI 依赖，
   用例里 `app.dependency_overrides[get_llm_client] = lambda: 假客户端`
   就完事；如果路由里直接 `httpx.post(...)`，测试只能去 monkeypatch
   第三方库内部函数，既脆又会漏（详见 tests/conftest.py 的 mock_llm）。

测试硬规矩：**任何用例都不允许发出真实网络请求**。真实调用会慢、
会不稳定（对端限流就红）、还会花钱；一个"偶尔红"的测试套件很快
就没人看了。
"""

from functools import lru_cache
from typing import Any

import httpx

from app.config import settings


class LLMError(RuntimeError):
    """上游模型服务调用失败（未配置密钥、超时、5xx、返回体不合法）。

    只在这一层出现：路由把它翻译成 502 + `LLM_UNAVAILABLE`，
    客户端拿到的是"上游不可用"，而不是一段 Python 异常文本。
    """


class LLMClient:
    """OpenAI 兼容接口的最小客户端（只实现 chat completions）。

    Args:
        base_url: 兼容接口根地址，如 https://api.deepseek.com/v1。
        api_key: 上游密钥。
        timeout: 单次调用超时秒数。
    """

    def __init__(self, base_url: str, api_key: str, timeout: float = 30.0) -> None:
        """保存连接参数，**不**在这里发任何请求（构造必须是零成本的）。"""
        # rstrip 防止 base_url 结尾多一个 / 拼出 //chat/completions，
        # 部分网关对重复斜杠会直接 404
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key
        self.timeout = timeout

    def chat(self, payload: dict[str, Any]) -> str:
        """同步调用 /chat/completions，返回第一条候选的文本。

        Args:
            payload: `ChatRequest.to_llm_payload()` 生成的请求体。

        Returns:
            str: 模型回复正文。

        Raises:
            LLMError: 未配置密钥、网络/超时/非 2xx、返回体结构不符合预期。
        """
        if not self._api_key:
            # 启动时校验过配置更好，但客户端可能被别的入口构造；这里再挡一次，
            # 免得把 "Bearer " 这种空凭证发给上游，得到一个莫名其妙的 401
            raise LLMError("未配置 LLM_API_KEY")

        try:
            response = httpx.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=self.timeout,
                # 不吃系统代理环境变量：本机开启 HTTP_PROXY 时，httpx 复用连接
                # 会把请求行写成绝对 URL，上游按路径匹配直接 404（实测踩过）
                trust_env=False,
            )
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as exc:
            # 超时、连接失败、4xx/5xx 全归一类：调用方只关心"上游没给出结果"
            raise LLMError(f"上游模型服务调用失败: {exc}") from exc
        except ValueError as exc:
            raise LLMError("上游返回体不是合法 JSON") from exc

        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            # 兼容接口千奇百怪，宁可在这里报"结构不符"，也不要在路由里
            # 解包时抛 KeyError 变成 500 —— 那会被误判成我们的 bug
            raise LLMError("上游返回体缺少 choices[0].message.content") from exc

        return str(content)


class MockLLMClient(LLMClient):
    """假模型：不发网络请求，把最后一条用户消息回显（LLM_MOCK=true 时启用）。

    和 tests/conftest.py 的 FakeLLMClient 是两件事，别混：

    - 测试用 FakeLLMClient 是靠 `app.dependency_overrides` 在**进程内**替换依赖，
      只对 pytest 里那个 TestClient 生效；
    - 这个 MockLLMClient 是通过配置打开的真实依赖，对 uvicorn / curl / smoke.sh
      同样生效 —— 因为那些调用方没法用 dependency_overrides。

    继承 LLMClient 而不是另起一个类，是为了让路由里的类型标注
    `Annotated[LLMClient, Depends(get_llm_client)]` 依然成立。
    """

    def __init__(self) -> None:
        """构造一个不发请求的客户端（base_url 用 mock:// 占位，不会被访问）。"""
        super().__init__(base_url="mock://local", api_key="mock", timeout=0.0)

    def chat(self, payload: dict[str, Any]) -> str:
        """返回固定格式的回显文本，签名与返回值类型与真实客户端一致。

        Args:
            payload: 与真实调用相同的请求体（含 messages）。

        Returns:
            str: "[mock] 收到：<用户最后一句>"。
        """
        messages = payload.get("messages") or []
        last_user = ""
        for item in reversed(messages):
            if isinstance(item, dict) and item.get("role") == "user":
                last_user = str(item.get("content", ""))
                break
        return f"[mock] 收到：{last_user}"


# 模块级单例：假客户端无状态，没必要每个请求新建
_MOCK_CLIENT = MockLLMClient()


@lru_cache
def _build_llm_client() -> LLMClient:
    """构造并缓存客户端（一个进程一个，复用连接池与 TLS 会话）。

    Returns:
        LLMClient: 进程级复用的客户端实例。
    """
    return LLMClient(
        base_url=settings.LLM_BASE_URL,
        api_key=settings.LLM_API_KEY,
        timeout=settings.LLM_TIMEOUT_SECONDS,
    )


def get_llm_client() -> LLMClient:
    """FastAPI 依赖：提供 LLM 客户端（测试里靠 override 换成假客户端）。

    Returns:
        LLMClient: LLM_MOCK=true 时返回假客户端，否则返回进程级复用的真实客户端。
    """
    if settings.LLM_MOCK:
        return _MOCK_CLIENT
    return _build_llm_client()
