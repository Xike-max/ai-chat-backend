"""响应模型的兼容入口（知识点 6、9、12）。

响应模型现在统一在 app/schemas.py 里定义（请求模型和响应模型放一个文件，
字段改动时一处就能看全）。本文件保留下来只做转发，原因有两点：

1. 已有路由（routers/conversations.py、routers/users.py）都写着
   `from app.schemas_response import ...`，转发能避免大面积改 import；
2. 万一以后想把响应模型单独拆出去，这里也是唯一需要改的地方。

**不要在这里重新定义同名类**：两份 MessageOut 迟早会分叉，
届时 response_model 用的是哪一份、字段以谁为准，会变成很难查的问题。
UserOut 就吃过这个亏 —— 它原本单独定义在这里，字段和 models.User 对不上
（缺 username），注册接口 return User 时会直接 500。
"""

from app.schemas import (
    ConversationDetail,
    ConversationOut,
    MessageOut,
    Page,
    TokenOut,
    UserCreate,
    UserOut,
)

__all__ = [
    "ConversationDetail",
    "ConversationOut",
    "MessageOut",
    "Page",
    "TokenOut",
    "UserCreate",
    "UserOut",
]
