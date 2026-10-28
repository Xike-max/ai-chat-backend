"""模型白名单路由（知识点 5）。

模型名用 Enum 收敛，非法值由 FastAPI 自动返回 422，业务代码里不需要
手写 if model not in ALLOWED 这种校验。
"""

from __future__ import annotations

from enum import Enum

from fastapi import APIRouter, Path
from pydantic import BaseModel, Field

router = APIRouter(prefix="/models", tags=["模型"])


class ModelName(str, Enum):
    """可用的 LLM 模型白名单。"""

    # 为什么必须同时继承 str，而不只是 Enum：
    # 1. 枚举成员本身就是字符串，可直接比较、拼接、写进 JSON，不必到处 .value；
    # 2. 序列化输出 "deepseek-chat" 而不是 {"value": "deepseek-chat"}，
    #    纯 Enum 在 JSON 里会变成对象，前端拿到的是脏数据；
    # 3. OpenAPI 才能生成 type: string + enum 约束，
    #    Swagger UI 才会把参数渲染成下拉框而不是自由输入框。

    DEEPSEEK_CHAT = "deepseek-chat"
    QWEN_PLUS = "qwen-plus"
    GPT_4O_MINI = "gpt-4o-mini"


class ModelInfo(BaseModel):
    """单个模型的可选信息。"""

    name: ModelName = Field(description="模型标识，调用模型接口时传这个值")
    display_name: str = Field(description="展示名称")
    provider: str = Field(description="提供方")
    context_window: int = Field(description="上下文窗口大小（token）")
    description: str = Field(description="一句话说明适用场景")


MODEL_CATALOG: dict[ModelName, ModelInfo] = {
    ModelName.DEEPSEEK_CHAT: ModelInfo(
        name=ModelName.DEEPSEEK_CHAT,
        display_name="DeepSeek Chat",
        provider="DeepSeek",
        context_window=64_000,
        description="中文理解好、价格低，适合日常对话与长文总结。",
    ),
    ModelName.QWEN_PLUS: ModelInfo(
        name=ModelName.QWEN_PLUS,
        display_name="通义千问 Plus",
        provider="阿里云",
        context_window=32_000,
        description="中文场景稳定，适合企业知识问答。",
    ),
    ModelName.GPT_4O_MINI: ModelInfo(
        name=ModelName.GPT_4O_MINI,
        display_name="GPT-4o mini",
        provider="OpenAI",
        context_window=128_000,
        description="多语言与工具调用能力强，适合复杂指令。",
    ),
}


@router.get("", response_model=list[ModelInfo], summary="获取可选模型列表")
async def list_models() -> list[ModelInfo]:
    """由前端的模型选择菜单调用，返回全部可用模型及其展示信息。"""
    return list(MODEL_CATALOG.values())


@router.get("/{model_name}", response_model=ModelInfo, summary="获取单个模型信息")
async def get_model(
    model_name: ModelName = Path(description="模型标识，只能是白名单内的值"),
) -> ModelInfo:
    """由前端在选择模型后拉取详情调用，返回该模型的展示信息与上下文窗口。

    传白名单外的值（如 gpt-5）时，FastAPI 会在进入函数前直接返回 422。
    """
    return MODEL_CATALOG[model_name]
