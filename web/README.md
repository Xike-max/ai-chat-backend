# AI Chat 验收台（前端）

Vite + React + TypeScript 实现的聊天验收台，用于直观检查 `ai-chat-backend` 后端各项能力：认证、会话管理、对话、错误契约、可观测性。

零 UI 框架依赖，只用 `react` / `react-dom` / `vite` 三个核心包。

## 快速开始

### 前置条件

- Node.js ≥ 18（本机实测 22.22.2）
- 后端已在 `http://127.0.0.1:8000` 启动（见根目录 `README.md`）

### 启动

```bash
cd web
npm install
npm run dev
```

浏览器打开 **http://localhost:5173**。首次使用先切到「注册」Tab 注册一个账号（用户名仅限字母数字），注册后自动登录。

> **代理说明**：`vite.config.ts` 把 `/api` 和 `/internal` 都代理到 `http://127.0.0.1:8000`，浏览器里走的是同源请求，因此**无需改动后端 CORS 配置**，也不会触发预检。

### 构建与预览

```bash
npm run build      # tsc 类型检查 + vite 产物打包，产物在 dist/
npm run preview    # 本地预览构建产物
```

## 功能

| 功能 | 说明 |
|---|---|
| 登录 / 注册 | OAuth2 表单登录换 JWT，token 持久化，侧边栏底部显示剩余有效期倒计时 |
| 会话列表 | 新建 / 搜索（300ms 防抖）/ 删除 / 加载更多，选中态绿色高亮 |
| 对话区 | 收发消息、模型切换、发送中占位、失败重试、自动滚动到底 |
| 请求日志抽屉 | 每次请求一行（方法 / 路径 / 状态码 / 耗时 / trace_id），展开看请求响应原文，Authorization 脱敏，trace_id 一键复制 |
| 接口检查页 | 16 个接口配置表驱动，六种错误（401/404/409/422/500/502）可手动触发 |
| 健康状态灯 | 15 秒轮询 `/api/v1/health`，绿 / 红两态 |

## 目录结构

```
web/
├── index.html                 # 入口 HTML
├── vite.config.ts             # 代理配置（/api、/internal → 8000）
├── package.json
├── tsconfig*.json
└── src/
    ├── main.tsx               # React 挂载入口
    ├── App.tsx                # 顶层状态机：booting/loggedIn/loggedOut/offline 四态
    ├── api.ts                 # fetch 封装 + 错误契约解析 + 请求日志（三合一）
    ├── types.ts               # 与后端对齐的 TS 类型
    ├── storage.ts             # token / 模型 / 用户名 的 localStorage 读写
    ├── requestLog.ts          # useSyncExternalStore 日志 store
    ├── endpoints.ts           # 接口检查页的 16 个接口配置表
    ├── styles.css             # 全局样式（WorkBuddy 风格：米白底 + 品牌绿）
    └── components/
        ├── TopBar.tsx         # 顶栏：品牌 / 健康灯 / 模型选择 / 日志入口
        ├── Sidebar.tsx        # 左侧会话列表 + 用户信息
        ├── ChatPanel.tsx      # 对话区：消息收发 + 悬浮输入框
        ├── MessageBubble.tsx  # 消息渲染（用户块 / AI 平铺 + 操作条）
        ├── LoginPage.tsx      # 登录 / 注册双 Tab
        ├── NetworkDrawer.tsx  # 请求日志抽屉
        ├── ApiInspector.tsx   # 接口检查页
        ├── ModelPicker.tsx    # 模型下拉选择
        ├── HealthBadge.tsx    # 健康状态灯
        ├── JsonBlock.tsx      # JSON 展示块（带复制）
        └── Toast.tsx          # 全局提示
```

## 错误契约对接

后端所有非 2xx 响应统一为：

```json
{ "error": { "code": "...", "message": "...", "detail": "..." } }
```

`api.ts` 把 401 单独抛出 `UnauthorizedError`（前端据此清 token、回登录页），网络异常抛 `NetworkError`，其余抛 `ApiError`，并把后端中文 `message` 原样透传给界面。

## 已知限制（后端接口决定的）

- `POST /chat` 是同步返回，无 SSE/WebSocket，**无流式输出**；发送中显示"正在思考"，发送中禁用输入框（最长等 30 秒）
- `GET /conversations/{id}` 固定只返回最近 50 条，对话区底部会提示「仅显示最近 50 条消息」
- `POST /conversations` 的 `model` 不落库，模型是全局选择，不能每个会话固定一个模型
- 无 `PATCH /conversations/{id}`，不能改名、归档，只能在新建时填标题
- `DELETE` 返回 204 且无 body，成功与否只能看状态码
- `/auth/token` 必须用 `URLSearchParams` 表单提交（OAuth2 密码流），手写 JSON 会 422
