# AI Chat Backend

基于 FastAPI 的 AI 对话后端服务：JWT 认证、会话/消息持久化、Alembic 迁移、统一错误契约。

技术栈：FastAPI 0.115 · Pydantic v2 · SQLAlchemy 2.0 · Alembic · PyJWT · bcrypt · SQLite / PostgreSQL

## 快速开始

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env          # 按需修改；.env 不进仓库
alembic upgrade head          # 建表

uvicorn app.main:app --reload --port 8000
```

打开 http://127.0.0.1:8000/docs，右上角 **Authorize** 可直接用用户名密码换 token 调接口。

## 目录结构

```
app/
  main.py           应用入口：路由挂载、CORS、异常处理器注册
  config.py         pydantic-settings 配置（含生产环境密钥守卫）
  database.py       engine / SessionLocal / Base / get_db
  models.py         User / Conversation / Message（UTCDateTime、级联删除）
  schemas.py        请求模型与响应模型（响应模型即字段白名单）
  crud.py           数据访问层，所有查询强制带 user_id 过滤
  deps.py           依赖零件：分页、API Key、OAuth2 + get_current_user
  auth.py           密码哈希（bcrypt）与 JWT 签发/校验
  errors.py         统一错误格式 + 四个异常处理器
  logging_conf.py   日志初始化
  routers/          health / auth / users / conversations / models / internal
alembic/            数据库迁移
scripts/            联调脚本
tests/              测试
```

## 接口一览

| 方法 | 路径 | 说明 | 鉴权 |
|---|---|---|---|
| GET | `/api/v1/health` | 健康检查 | 无 |
| POST | `/api/v1/auth/register` | 注册，返回 201 | 无 |
| POST | `/api/v1/auth/token` | 登录换 token（表单提交） | 无 |
| GET | `/api/v1/users/me` | 当前用户 | Bearer |
| GET | `/api/v1/conversations` | 会话列表（筛选 + 分页） | 无 |
| GET | `/api/v1/models` | 模型白名单 | 无 |
| GET | `/internal/health/deep` | 深度健康检查（含 DB） | X-API-Key |
| POST | `/internal/usage/report` | 用量上报 | X-API-Key |

```bash
# 注册 + 登录 + 调用受保护接口
curl -X POST http://127.0.0.1:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username":"alice","email":"alice@example.com","password":"correct-horse-battery"}'

TOKEN=$(curl -s -X POST http://127.0.0.1:8000/api/v1/auth/token \
  -d "username=alice&password=correct-horse-battery" | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")

curl http://127.0.0.1:8000/api/v1/users/me -H "Authorization: Bearer $TOKEN"
```

## 错误格式

所有非 2xx 响应结构一致，前端只需一套解析：

```json
{"error": {"code": "VALIDATION_ERROR", "message": "请求参数校验失败",
           "detail": [{"field": "body.message", "message": "必填"}]}}
```

- `code`：机器可读的稳定错误码，用于分支判断
- `message`：可直接展示的提示
- `detail`：结构化补充；校验错误是 `[{field, message}]`，500 是 `{trace_id}`

## 部署注意

- `.env` 永不入库；`SECRET_KEY` / `API_KEY` 必须由环境变量或密钥管理服务注入
- `ENV=prod` 时，默认或过短的 `SECRET_KEY` 会让应用**拒绝启动**
- 登录接口尚未做限流，上线前应在网关或中间件层补齐
- `app/routers/demo_errors.py`（故意抛 500 的演示路由）仅非生产环境挂载
