#!/usr/bin/env bash
# ============================================================================
# 端到端冒烟：一条命令验证"注册 → 登录 → 建会话 → 对话 → 查历史 → 删除"。
#
# 它和 pytest 的分工：
#   pytest   验证函数级行为（进程内、内存库、依赖全部替换）
#   本脚本   验证"真的起了一个 HTTP 服务、真的走了一遍网络" —— 中间件、CORS、
#            路由挂载顺序、JSON 契约、迁移是否可执行，这些 pytest 都看不到。
#
# 默认使用 LLM_MOCK=true 的假模型：不联网、不花钱、不需要 API Key。
# 任何一步不符合预期，立刻输出 ❌ FAILED AT <step> 并 exit 1（CI 可直接用）。
#
# 用法：
#   bash scripts/smoke.sh              # 默认端口 8021
#   PORT=9000 bash scripts/smoke.sh    # 换端口
#   PYTHON=.venv/Scripts/python bash scripts/smoke.sh   # 指定解释器
# ============================================================================
set -uo pipefail

BASE="http://127.0.0.1:${PORT:-8021}"
PYTHON="${PYTHON:-python}"
DB_FILE=".smoke.db"
LOG_FILE=".smoke-server.log"
HEADERS=".smoke-headers.tmp"   # 放项目目录：Windows 上 mktemp 的 /tmp 路径
BODY=".smoke-body.tmp"        # 会让某些删除实现报"drive prefix"警告
SERVER_PID=""
KEEP_LOG=0

step() { printf '\n▶ %s\n' "$1"; }

fail() {
  printf '\n❌ FAILED AT %s\n' "$1" >&2
  [ -s "$BODY" ] && { printf '响应体：\n' >&2; cat "$BODY" >&2; printf '\n' >&2; }
  KEEP_LOG=1
  exit 1
}

cleanup() {
  if [ -n "$SERVER_PID" ]; then
    kill "$SERVER_PID" 2>/dev/null
    wait "$SERVER_PID" 2>/dev/null
  fi
  rm -f "$BODY" "$HEADERS" "$DB_FILE"
  if [ "$KEEP_LOG" = "1" ] && [ -f "$LOG_FILE" ]; then
    printf '服务日志保留在 %s（排查完请自行删除）\n' "$LOG_FILE" >&2
  else
    rm -f "$LOG_FILE"
  fi
}
trap cleanup EXIT INT TERM

# expect <期望状态码> <步骤名> <curl 参数...>
expect() {
  local want="$1" name="$2" code
  shift 2
  code="$(curl -s --noproxy '*' -o "$BODY" -w '%{http_code}' "$@")" || fail "$name（curl 执行失败）"
  if [ "$code" != "$want" ]; then
    printf '  期望 %s，实际 %s\n' "$want" "$code" >&2
    fail "$name"
  fi
  printf '  ✓ %s → %s\n' "$name" "$code"
}

json_str() { sed -n "s/.*\"$1\":\"\([^\"]*\)\".*/\1/p" "$BODY" | head -1; }
json_num() { sed -n "s/.*\"$1\":\([0-9][0-9]*\).*/\1/p" "$BODY" | head -1; }

# ---------------------------------------------------------------- 0. 准备数据库
step "0/8 准备临时数据库（alembic upgrade head）"
rm -rf "$DB_FILE" 2>/dev/null || true
DATABASE_URL="sqlite:///./$DB_FILE" "$PYTHON" -m alembic upgrade head >/dev/null 2>&1 \
  || { printf '  alembic 迁移失败，先在仓库根目录单独跑一次看报错\n' >&2; fail "alembic upgrade head"; }
printf '  ✓ 迁移完成 → %s\n' "$DB_FILE"

# ---------------------------------------------------------------- 1. 启动服务
step "1/8 启动服务（LLM_MOCK=true，不联网）"
DATABASE_URL="sqlite:///./$DB_FILE" LLM_MOCK=true DEBUG=false LOG_LEVEL=WARNING \
  "$PYTHON" -m uvicorn app.main:app --host 127.0.0.1 --port "${PORT:-8021}" \
  >"$LOG_FILE" 2>&1 &
SERVER_PID=$!

READY=""
for _ in $(seq 1 60); do
  if [ "$(curl -s --noproxy '*' -o /dev/null -w '%{http_code}' "$BASE/api/v1/health" 2>/dev/null)" = "200" ]; then
    READY=1
    break
  fi
  sleep 0.5
done
[ -n "$READY" ] || fail "服务启动（30 秒内 $BASE/api/v1/health 未返回 200）"
printf '  ✓ 服务就绪 → %s\n' "$BASE"

# ---------------------------------------------------------------- 2. 注册
step "2/8 注册"
expect 201 "注册" -X POST "$BASE/api/v1/auth/register" \
  -H 'Content-Type: application/json' \
  -d '{"username":"smoke","email":"smoke@example.com","password":"smoke-pass-123"}'
USER_ID="$(json_num id)"
[ -n "$USER_ID" ] || fail "注册（响应里没有用户 id）"
grep -qi 'hashed_password' "$BODY" && fail "注册（响应泄露了 hashed_password）"

# ---------------------------------------------------------------- 3. 登录
step "3/8 登录换 token"
expect 200 "登录" -X POST "$BASE/api/v1/auth/token" \
  -d 'username=smoke&password=smoke-pass-123'
TOKEN="$(json_str access_token)"
[ -n "$TOKEN" ] || fail "登录（响应里没有 access_token）"
AUTH="Authorization: Bearer $TOKEN"

# ---------------------------------------------------------------- 4. 鉴权生效
step "4/8 验证鉴权真的拦得住"
expect 401 "无 token 访问 /users/me" "$BASE/api/v1/users/me"
expect 200 "带 token 访问 /users/me" -H "$AUTH" "$BASE/api/v1/users/me"

# ---------------------------------------------------------------- 5. 建会话
step "5/8 新建会话"
expect 201 "新建会话" -X POST "$BASE/api/v1/conversations" \
  -H "$AUTH" -H 'Content-Type: application/json' -d '{"title":"冒烟测试会话"}'
CONV_ID="$(json_num id)"
[ -n "$CONV_ID" ] || fail "新建会话（响应里没有会话 id）"

# ---------------------------------------------------------------- 6. 对话（mock）
step "6/8 发送消息（mock 模型）"
expect 200 "发送消息" -X POST "$BASE/api/v1/conversations/$CONV_ID/chat" \
  -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"message":"你好，这是一条冒烟测试消息"}'
grep -q '\[mock\]' "$BODY" || fail "发送消息（回复里没有 [mock] 前缀，说明 LLM_MOCK 没生效）"

# ---------------------------------------------------------------- 7. 查历史
step "7/8 查询会话历史"
expect 200 "查询会话详情" -H "$AUTH" "$BASE/api/v1/conversations/$CONV_ID"
grep -q '"message_count":2' "$BODY" || fail "查询会话详情（message_count 不是 2，用户消息或回复没落库）"
grep -q '冒烟测试消息' "$BODY" || fail "查询会话详情（历史里找不到刚发的那句话）"

# ---------------------------------------------------------------- 8. 删除
step "8/8 删除会话并确认已消失"
expect 204 "删除会话" -X DELETE -H "$AUTH" "$BASE/api/v1/conversations/$CONV_ID"
expect 404 "再次查询（应 404）" -H "$AUTH" "$BASE/api/v1/conversations/$CONV_ID"

# 顺带确认追踪头存在：没有它，线上出问题就没有 id 可以对齐日志
curl -s --noproxy '*' -D "$HEADERS" -o /dev/null "$BASE/api/v1/health"
grep -qi '^x-trace-id:' "$HEADERS" || fail "响应头 X-Trace-Id（追踪中间件没生效）"
printf '  ✓ X-Trace-Id 响应头存在\n'

printf '\n✅ ALL PASS\n'
