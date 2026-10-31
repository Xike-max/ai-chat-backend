# AI Chat Backend

面向 AI 对话场景的生产级后端：JWT 认证、会话/消息持久化、统一错误契约、trace_id 贯穿的日志、
Alembic 迁移、pytest + 冒烟脚本双保险。

技术栈：FastAPI 0.115 · Pydantic v2 · SQLAlchemy 2.0 · Alembic · PyJWT · bcrypt · SQLite / PostgreSQL

## 快速开始（3 条命令）

前提：Python 3.11+，在仓库根目录执行。

```bash
pip install -r requirements.txt
cp .env.example .env && alembic upgrade head
uvicorn app.main:app --port 8000
```

Windows PowerShell 用这三条（`cp` 换成 `Copy-Item`，其余相同）：

```powershell
pip install -r requirements.txt
Copy-Item .env.example .env; alembic upgrade head
uvicorn app.main:app --port 8000
```

打开 http://127.0.0.1:8000/docs ，右上角 **Authorize** 填注册时的用户名密码即可换 token 调接口。

> **启动报 `ModuleNotFoundError: No module named 'jwt'` 之类的错，几乎都是解释器错位。**
> 裸 `uvicorn` 取的是 PATH 里第一个同名可执行文件，可能来自另一套 Python 安装（比如 `D:\Python314`），
> 那套环境里没有本项目依赖，import 阶段就崩，堆栈最后一帧停在 `uvicorn/importer.py`。
> 两个办法：① 一律用 `python -m uvicorn app.main:app --port 8000`，保证"装包的解释器"和"跑服务的解释器"是同一个；
> ② 先自检 `python -c "import fastapi, jwt, bcrypt, sqlalchemy; print('deps ok')"`，报错就说明该解释器缺依赖。
> 另外 **bcrypt 必须留在 4.0.1**：5.x 会让 passlib 初始化后端时直接抛
> `ValueError: password cannot be longer than 72 bytes`，注册/登录全线不可用（详见 requirements.txt 注释）。

想先在本地跑通"对话"而不配置 API Key？把 `.env` 里 `LLM_MOCK=true` 打开（或启动时加
`LLM_MOCK=true uvicorn ...`），模型会变成回显，其余链路完全真实。

### 前端验收台

`web/` 下是一个 React 前端验收台，用于直观检查后端能力（登录注册、会话、对话、错误契约、请求日志）。

```bash
cd web
npm install
npm run dev    # 打开 http://localhost:5173
```

它通过 Vite 代理把 `/api`、`/internal` 转发到 `127.0.0.1:8000`，走同源请求，**后端 CORS 无需改动**。
详细说明见 [`web/README.md`](web/README.md)。

## 架构说明

分层的唯一目的：让"改一处"只影响一处。依赖方向永远单向向下，**下面不许反过来 import 上面**。

| 层 | 文件 | 只负责 | 不该出现 |
|---|---|---|---|
| 入口装配 | `app/main.py` | lifespan、中间件、异常处理器、路由挂载 | 任何 SQL、任何业务判断 |
| 路由 | `app/routers/*.py` | 解析参数、调用依赖、把结果翻译成响应/状态码 | `db.query` / `select()`、`jwt`、`httpx` |
| 依赖零件 | `app/deps.py` | 分页、Bearer 解析、当前用户、API Key | 业务规则 |
| 数据访问 | `app/crud.py` | SQL 与 ORM，**按 id 查必带 user_id** | `HTTPException` |
| 领域能力 | `app/auth.py` / `app/llm.py` | 密码哈希/JWT、上游模型调用 | 依赖 FastAPI 请求对象 |
| 基础设施 | `config` / `database` / `errors` / `logging_conf` / `middleware` | 配置、连接、错误契约、日志、追踪 | 业务语义 |

一次请求的完整路径：

```
客户端
  → TraceMiddleware（生成/透传 X-Trace-Id，写 contextvar，记耗时）
  → CORSMiddleware
  → 路由依赖链：Pagination → get_current_user（oauth2_scheme → decode_token → 查库 → is_active）
  → 路由函数：crud.* / llm.chat()
  → response_model 按字段白名单序列化
  → 异常时：AppError / RequestValidationError / HTTPException / Exception 四个处理器统一成
     {"error": {"code", "message", "detail"}}
```

```
app/
  main.py            装配层（≤60 行，只有 lifespan / 中间件 / 异常处理器 / include_router）
  config.py          pydantic-settings 配置 + 生产环境密钥守卫
  database.py        engine / SessionLocal / Base / get_db
  models.py          User / Conversation / Message（UTCDateTime、级联删除、派生 message_count）
  schemas.py         请求与响应模型（响应模型 = 字段白名单）
  crud.py            数据访问层
  auth.py            bcrypt 哈希 + JWT 签发/校验
  deps.py            Pagination / verify_api_key / get_current_user / CurrentUser
  errors.py          统一错误契约 + 四个异常处理器
  logging_conf.py    dictConfig 日志 + trace_id Filter
  middleware.py      纯 ASGI 的 TraceMiddleware
  llm.py             OpenAI 兼容客户端 + MockLLMClient
  routers/           health / auth / users / conversations / chat / models / internal / root
alembic/             数据库迁移
scripts/             call_internal.py（内部接口联调）、smoke.sh（端到端冒烟）、启动脚本
tests/               pytest 用例（内存库 + 假模型）
web/                 React 前端验收台（Vite + TS，见 web/README.md）
```

## 接口一览

| 方法 | 路径 | 说明 | 鉴权 |
|---|---|---|---|
| GET | `/` | 服务入口（指路牌） | 无 |
| GET | `/api/v1/health` | 健康检查（不碰数据库） | 无 |
| POST | `/api/v1/auth/register` | 注册，201 | 无 |
| POST | `/api/v1/auth/token` | 登录换 token（表单提交） | 无 |
| GET | `/api/v1/users/me` | 当前用户 | Bearer |
| GET | `/api/v1/conversations` | 会话列表（关键词/归档筛选 + 分页） | Bearer |
| POST | `/api/v1/conversations` | 新建会话，201 | Bearer |
| GET | `/api/v1/conversations/latest` | 最近活跃会话 | Bearer |
| GET | `/api/v1/conversations/{id}` | 会话详情（含最近 50 条消息） | Bearer |
| DELETE | `/api/v1/conversations/{id}` | 删除会话，204 | Bearer |
| POST | `/api/v1/conversations/{id}/chat` | 会话内对话（调 LLM 并落库两条消息） | Bearer |
| GET | `/api/v1/models` | 模型白名单 | Bearer |
| GET | `/api/v1/demo/echo` | 依赖用法演示 | Bearer + X-API-Key |
| GET | `/internal/health/deep` | 深度健康检查（真的执行 `SELECT 1`） | X-API-Key |
| POST | `/internal/usage/report` | 用量上报，202 | X-API-Key |

仅非生产环境挂载：`/api/v1/demo/errors/*`（演示 403/404/409/500 统一格式）、
`/api/v1/debug/log-test`（**需** `DEBUG=true`）。两者同样要求 Bearer，避免匿名者刷日志、造错误率。

```bash
# 注册 → 登录 → 调受保护接口
curl -X POST http://127.0.0.1:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username":"alice","email":"alice@example.com","password":"correct-horse-battery"}'

TOKEN=$(curl -s -X POST http://127.0.0.1:8000/api/v1/auth/token \
  -d "username=alice&password=correct-horse-battery" | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")

curl http://127.0.0.1:8000/api/v1/users/me -H "Authorization: Bearer $TOKEN"
```

## 冒烟脚本

```bash
bash scripts/smoke.sh          # 默认端口 8021，可用 PORT=9000 覆盖
```

它自己建临时库（`alembic upgrade head`）、以 `LLM_MOCK=true` 起服务，然后逐步验证
注册 → 登录 → 401 拦截 → 建会话 → 发消息 → 查历史 → 删除 → 确认 404 → `X-Trace-Id` 存在。
每步都比对状态码，全过输出 `✅ ALL PASS`，任一步失败输出 `❌ FAILED AT <step>` 并 `exit 1`，
同时保留 `.smoke-server.log` 供排查。

## 运行测试

```bash
pytest
```

- 15 个用例，约 1 秒。内存 SQLite（`sqlite://` + `StaticPool`），不碰 `ai_chat.db`，
  连跑两次结果完全一致
- LLM 由 `mock_llm` 夹具替换成假客户端：不发真实请求、不花钱
- 用例之间零共享状态，单独跑某一条也行：
  `pytest tests/test_chat.py::test_chat_to_another_users_conversation_is_404`

## 错误格式

所有非 2xx 响应结构一致，前端只需一套解析：

```json
{"error": {"code": "VALIDATION_ERROR", "message": "请求参数校验失败",
           "detail": [{"field": "body.message", "message": "必填"}]}}
```

- `code`：机器可读的稳定错误码（`UNAUTHORIZED` / `CONVERSATION_NOT_FOUND` / `LLM_UNAVAILABLE` …），
  分支判断只能用它，不能用 `message`
- `message`：可直接展示的中文提示
- `detail`：结构化补充；校验错误是 `[{field, message}]`，500 是 `{trace_id}`
- 401 保留 `WWW-Authenticate: Bearer`，405 保留 `Allow`（统一格式不会吞掉协议头）

## 已知限制

如实列出，**都还没做**，上线前请逐条评估：

1. **没有 refresh token**。access token 到期（默认 60 分钟）就得重新输密码，没有静默续期；
   也没有服务端吊销/黑名单，只能靠 `is_active` 封号让存量 token 立即失效。
2. **登录接口没有限流**。`/auth/token` 可被在线撞库，目前只靠 bcrypt 的 ~250ms 成本拖延。
   应在网关或 `slowapi` 中间件按 IP + 账号双维度限流（横切关注点，不要写进业务函数）。
3. **LLM 调用没有重试、没有流式、没有熔断**。上游一次抖动就是一次 502；
   长回答只能等完整结果返回，首字延迟高。也没有 token 计费统计。
4. **SQLite 不适合生产并发**。默认库是单写锁，多 worker 下写会排队甚至 `database is locked`；
   生产请换 PostgreSQL（`DATABASE_URL` 改一行即可，迁移脚本同一套）。
5. **历史上下文是硬截断**。每轮只带最近 20 条消息，没有摘要压缩，长会话会"忘记"早期内容；
   上下文长度也不按 token 计算，可能超出模型窗口。
6. **`conversations.model` 没有落库**。建会话时接收该字段只是为了契约稳定，实际未持久化；
   要真正支持"每个会话固定模型"需要加列 + 迁移。
7. **分页是 offset 型**。深翻页（`skip` 很大）在数据量大时变慢，且并发写入会导致漏项/重项；
   正确做法是游标分页（`created_at + id`）。
8. **没有审计日志**。登录成功/失败、封号、删除会话都没进独立审计表，事后追责只能翻 stdout 日志。

## 部署注意

- `.env` 永不入库；`SECRET_KEY` / `API_KEY` / `LLM_API_KEY` 必须由环境变量或 KMS 注入
- `ENV=prod` 时，默认或过短的 `SECRET_KEY`、或 `DEBUG=true`，都会让应用**拒绝启动**
- 容器只往 stdout 写日志（12-factor），采集交给平台；线上建议 `LOG_JSON=true`
- 上线前跑一遍 `bash scripts/smoke.sh` 与 `pytest`，两者都绿再发布
