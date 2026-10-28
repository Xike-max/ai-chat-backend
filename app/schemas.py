"""对话相关的请求模型（知识点 5、6、9）。

Pydantic v2 写法约定：
- 约束一律写在 Field() 里（min_length / max_length / ge / le），不写 validator 里的 if；
- 不使用 v1 的旧写法（字典导出旧方法、内嵌 Config 类、ORM 模式开关），
  对应改为 model_dump() / ConfigDict / from_attributes=True。

字段名对齐 OpenAI 兼容接口（DeepSeek、通义千问都吃这套），
所以是 temperature / max_tokens / top_p，不要自创 max_length、temp 这类名字。
"""

from datetime import datetime
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

# 密码长度的唯一判定逻辑在 app/auth.py（贴着 bcrypt 的 72 字节截断点），
# 这里只负责在 HTTP 边界上调用它，把违规输入变成 422 而不是等到入库才炸。
from app.auth import check_password_length

# 消息角色：与 OpenAI messages.role 完全一致
Role = Literal["system", "user", "assistant"]

# 分页外壳的泛型参数（Page[ConversationOut] / Page[MessageOut]）
T = TypeVar("T")


class MessageIn(BaseModel):
    """单条对话消息。

    content 上限 4000 字符的依据：
    中文场景下 1 个汉字 ≈ 1 token（国产模型分词器基本如此），4000 字符 ≈ 4000 token。
    主流模型上下文 32k~128k，单条消息占 4000 已经不低；一次请求还要放
    历史消息（按 20 条算很容易上万 token）和 system prompt（通常 200~500 token）。
    上限设得更高，用户随手粘一篇长文就会把上下文顶爆，模型侧报
    context_length_exceeded 极难排查；在接口层就用 4000 拦住，错误定位清晰得多。
    """

    model_config = ConfigDict(
        json_schema_extra={
            # Swagger UI 的 “Example Value” 只读单数 example，列表给 Schemas 面板用
            "example": {"role": "user", "content": "帮我把这段话改得更正式一点。"},
            "examples": [
                {"role": "user", "content": "帮我把这段话改得更正式一点。"},
                {"role": "assistant", "content": "可以，请把原文发我。"},
            ],
        }
    )

    role: Role = Field(default="user", description="消息角色：system / user / assistant")
    content: str = Field(
        min_length=1,
        max_length=4000,
        description="消息正文，1~4000 字符",
        examples=["你好，帮我总结一下这份会议纪要。"],
    )


class ChatOptions(BaseModel):
    """采样参数，字段名与 OpenAI 兼容接口逐字对应。

    作为 ChatRequest 的可选字段出现；不传时由服务端用这里的默认值兜底，
    因此每个字段都给了 default，不用 None 表达“未设置”。
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {"temperature": 0.5, "max_tokens": 1024, "top_p": 0.9},
            "examples": [
                {"temperature": 0.5, "max_tokens": 1024, "top_p": 0.9},
                {"temperature": 0, "max_tokens": 256},
            ],
        }
    )

    temperature: float = Field(
        default=0.7,
        ge=0,
        le=2,
        description="采样温度，0 最稳定、2 最发散；上限跟 OpenAI 保持一致",
        examples=[0.7],
    )
    max_tokens: int = Field(
        default=1024,
        ge=1,
        le=8192,
        description="本次回复的最大 token 数",
        examples=[1024],
    )
    top_p: float = Field(
        default=1.0,
        ge=0,
        le=1,
        description="核采样阈值，一般只调 temperature 或 top_p 其中之一",
        examples=[1.0],
    )


class ChatRequest(BaseModel):
    """一次对话请求体（POST /conversations/{id}/chat 的 body）。"""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "message": "用一句话解释什么是向量数据库",
                "model": "deepseek-chat",
                "conversation_id": 1,
                "options": {"temperature": 0.5, "max_tokens": 512},
            },
            "examples": [
                {
                    "message": "用一句话解释什么是向量数据库",
                    "model": "deepseek-chat",
                    "conversation_id": 1,
                    "options": {"temperature": 0.5, "max_tokens": 512},
                },
                {
                    "message": "继续刚才的话题，再举个实际例子",
                    "model": "qwen-plus",
                    "conversation_id": 2,
                },
            ],
        }
    )

    message: str = Field(
        min_length=1,
        max_length=4000,
        description="用户本轮输入，1~4000 字符",
        examples=["用一句话解释什么是向量数据库"],
    )
    model: str = Field(
        default="deepseek-chat",
        min_length=1,
        max_length=64,
        description="模型标识，如 deepseek-chat / qwen-plus / gpt-4o-mini",
        examples=["deepseek-chat"],
    )
    conversation_id: int | None = Field(
        default=None,
        ge=1,
        description="续聊的会话 ID；新会话传 null",
        examples=[1],
    )
    system_prompt: str | None = Field(
        default=None,
        max_length=2000,
        description="系统提示词，不传则用服务端默认人格设定",
        examples=["你是一位严谨的后端工程师，回答尽量简短。"],
    )
    options: ChatOptions | None = Field(
        default=None,
        description="采样参数；传 null 时由服务端填默认值（temperature=0.7、max_tokens=1024）",
    )

    def to_llm_payload(self, history: list[MessageIn]) -> dict[str, Any]:
        """转换成 OpenAI 兼容接口的 payload，只做转换，不发请求。

        Args:
            history: 该会话的历史消息，按时间正序；本轮 message 会追加到最后。

        Returns:
            dict[str, Any]: 可直接作为 POST {base_url}/chat/completions 的请求体，
            结构为 {"model": ..., "messages": [...], "temperature": ..., "max_tokens": ...}。
        """
        messages: list[dict[str, str]] = []
        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})

        # 按 role 原样组装历史，保证 user / assistant 的交替关系不被破坏
        messages.extend(
            {"role": item.role, "content": item.content} for item in history
        )
        messages.append({"role": "user", "content": self.message})

        # options 为 None 时用服务端默认值兜底，避免下游收到 null 参数
        options = self.options or ChatOptions()

        return {
            "model": self.model,
            "messages": messages,
            # exclude_none 防止把 None 传给下游（部分兼容接口会因此 400）
            **options.model_dump(exclude_none=True),
        }


class ConversationCreate(BaseModel):
    """新建会话的请求体（POST /conversations 的 body）。"""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {"title": "关于 RAG 的讨论", "model": "qwen-plus"},
            "examples": [
                {"title": "关于 RAG 的讨论", "model": "qwen-plus"},
                {"title": "新会话"},
            ],
        }
    )

    title: str = Field(
        default="新会话",
        min_length=1,
        max_length=200,
        description="会话标题，1~200 字符；不传则用默认标题",
        examples=["关于 RAG 的讨论"],
    )
    model: str = Field(
        default="deepseek-chat",
        min_length=1,
        max_length=64,
        description="该会话默认使用的模型",
        examples=["qwen-plus"],
    )


# ============================================================================
# 用户与认证（知识点 12）
# ============================================================================


class UserCreate(BaseModel):
    """注册请求体（POST /auth/register 的 body）。"""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "username": "alice",
                "email": "alice@example.com",
                "password": "correct-horse-battery",
            }
        }
    )

    username: str = Field(
        min_length=3,
        max_length=50,
        # 只允许字母数字和 _ . - 三种符号：中文、空格、emoji 做用户名会带来
        # 一堆麻烦（URL 编码、前后端 trim 规则不一致、看起来一模一样但字节不同的
        # 同形字冒充）。真要支持中文昵称，应该另开一个 display_name 字段，
        # 而不是把登录标识符做成自由文本。
        pattern=r"^[A-Za-z0-9_.-]+$",
        description="登录用户名，3~50 位，仅限字母、数字、下划线、点、连字符",
        examples=["alice"],
    )
    email: EmailStr = Field(
        # max_length 必须 <= users.email 的 VARCHAR(120)：
        # 邮箱是最长的合法地址可达 254 字符，不拦住就会在入库时被截断/报错
        max_length=120,
        description="邮箱，注册后可用于登录",
        examples=["alice@example.com"],
    )
    password: str = Field(
        # 这里只给字符级上限做个粗筛（防超长 body 白算哈希），
        # **真正生效的是下面按字节校验的 validator**：
        # 字符数和字节数不是一回事，一个汉字占 3 字节。
        max_length=72,
        description="密码，8~72 字节（bcrypt 只使用前 72 字节，超出会被静默截断）",
        examples=["correct-horse-battery"],
    )

    @field_validator("username")
    @classmethod
    def _strip_username(cls, value: str) -> str:
        """去掉用户名首尾空白，并把空串挡在这里。

        Args:
            value: 原始用户名。

        Returns:
            str: 去掉首尾空白后的用户名。

        Raises:
            ValueError: 去空白后为空（例如只输入了空格）。
        """
        # 用户在手机输入法里很容易带上尾随空格；不 strip 就会出现
        # "alice" 和 "alice " 两个账号，而人眼完全看不出来。
        # pattern 是在 strip 之后才校验的，所以 " alice " 不会被 pattern 拦下。
        value = value.strip()
        if not value:
            raise ValueError("用户名不能为空")
        return value

    @field_validator("password")
    @classmethod
    def _validate_password_bytes(cls, value: str) -> str:
        """按字节校验密码长度（8~72），堵住 bcrypt 静默截断的坑。

        Args:
            value: 用户提交的明文密码。

        Returns:
            str: 校验通过的原始密码。

        Raises:
            ValueError: 少于 8 字节或多于 72 字节。
        """
        # 刻意**不做** .strip() / 大小写归一化：
        # 密码是逐字节参与哈希的，任何"顺手规范化"都会造成
        # "我明明输对了却登不上"，而且注册与登录两侧的规则一旦不一致，
        # 会出现 "abc123 " 能注册、却只能用 "abc123" 登录的灵异事件。
        return check_password_length(value)


class TokenOut(BaseModel):
    """登录成功后的令牌响应（POST /auth/token 的返回值）。"""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                "token_type": "bearer",
                "expires_in": 3600,
            }
        }
    )

    access_token: str = Field(description="JWT 访问令牌")
    # 用 Literal 而不是 str：OAuth2 规范里这个值固定是 bearer，
    # 前端拼 Authorization 头时直接拿来用，不会被写成 "Bearer " 双份或 "token"
    token_type: Literal["bearer"] = Field(default="bearer", description="令牌类型，固定 bearer")
    # 有效期秒数：让客户端能主动在过期前刷新，而不是等某个请求 401 才发现
    expires_in: int = Field(description="有效期（秒）")


# ============================================================================
# 响应模型（知识点 6、9）
# ============================================================================
# 与 ORM 模型一一对应，但**只暴露该暴露的字段**：
# User.hashed_password 永远不会出现在这里，这就是"响应模型即白名单"。
# from_attributes=True 让 Pydantic 直接吃 ORM 对象（按属性取值，不要求是 dict），
# 所以 crud 返回 Conversation 实例，FastAPI 能直接按这些模型序列化。


class MessageOut(BaseModel):
    """单条消息的对外结构。"""

    model_config = ConfigDict(from_attributes=True)

    id: int = Field(description="消息 ID")
    role: Role = Field(description="消息角色：system / user / assistant")
    content: str = Field(description="消息正文")
    tokens: int = Field(description="本条消息消耗的 token 数，未知时为 0")
    created_at: datetime = Field(description="创建时间，ISO 8601 带时区")


class ConversationOut(BaseModel):
    """会话摘要（列表页 / 新建 / 详情页的公共部分）。"""

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "id": 1,
                "title": "关于 RAG 的讨论",
                "created_at": "2026-10-27T09:45:00Z",
                "message_count": 3,
                "summary": "讨论了向量检索与重排的取舍。",
            }
        },
    )

    id: int = Field(description="会话 ID")
    title: str = Field(description="会话标题")
    created_at: datetime = Field(description="创建时间，ISO 8601 带时区")
    # 来自 Conversation.message_count 这个只读派生属性（相关子查询算出），
    # 不是数据库列，也不需要额外查询
    message_count: int = Field(description="消息条数")
    # 可选字段：未生成摘要时为 None，配合 response_model_exclude_none=True
    # 让响应里干脆不出现这个 key，前端不用区分 null 和缺失
    summary: str | None = Field(default=None, description="会话摘要，未生成时为 null")


class ConversationDetail(ConversationOut):
    """会话详情：在摘要基础上带上全部消息。"""

    model_config = ConfigDict(from_attributes=True)

    messages: list[MessageOut] = Field(default_factory=list, description="按时间正序的消息列表")


class UserOut(BaseModel):
    """对外暴露的用户信息（注册响应、GET /users/me 共用）。

    这里**没有** hashed_password 字段——响应模型就是最后一道安全边界：
    即使业务代码 `return user`（ORM 对象带着密码哈希），序列化时也只会输出
    下面这几个字段。新增字段时忘了改这里，是被"漏掉"而不是被"泄露"，
    这个方向的失误才是可接受的。
    """

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "id": 1,
                "username": "alice",
                "email": "alice@example.com",
                "is_active": True,
                "created_at": "2026-10-01T08:00:00Z",
            }
        },
    )

    id: int = Field(description="用户 ID")
    username: str = Field(description="登录用户名")
    email: str = Field(description="登录邮箱")
    is_active: bool = Field(description="账号是否可用；被禁用后所有 token 立即失效")
    created_at: datetime = Field(description="注册时间，ISO 8601 带时区")


class Page(BaseModel, Generic[T]):
    """通用分页响应：{"total": n, "items": [...]}。

    用 Pydantic 泛型而不是给每种类型各写一个 Page 类：
    Page[ConversationOut]、Page[MessageOut] 共用同一套外壳，
    前端只需要认一种分页结构。
    """

    model_config = ConfigDict(from_attributes=True)

    total: int = Field(description="筛选后、分页前的总条数")
    items: list[T] = Field(description="当前页数据")


# ============================================================================
# 三个“故意非法”的 JSON 例子（POST body），以及 FastAPI 期望返回的 422 定位：
#
# 1) 空 body（不传 body）：期望 detail[0].loc == ["body"]
#    原因：body 本身缺失，还没走到字段级校验。
#
# 2) {"message": ""}：期望 detail[0].loc == ["body", "message"]
#    msg: String should have at least 1 character（min_length=1 生效）
#
# 3) {"message": "你好", "options": {"temperature": 3.0}}
#    期望 detail[0].loc == ["body", "options", "temperature"]
#    msg: Input should be less than or equal to 2（le=2 生效）
#
# 补充一个常见越界：message 传 4001 个字符
#    → loc == ["body", "message"]，msg: String should have at most 4000 characters
# ============================================================================
