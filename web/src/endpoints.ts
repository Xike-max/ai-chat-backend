// 16 个接口的配置表，检查页靠它驱动渲染，新增接口只加一条配置。

export type Endpoint = {
  group: string;
  id: string;
  label: string;
  method: "GET" | "POST" | "DELETE";
  path: string; // 含 {path_param} 占位
  pathParams?: { key: string; example: string }[];
  query?: { key: string; example: string }[];
  body?: unknown; // 默认 body 模板；undefined 表示无 body
  auth: "bearer" | "apikey" | "none";
  form?: boolean; // true → body 用 URLSearchParams
};

export const ENDPOINTS: Endpoint[] = [
  // ── 认证 ──
  {
    group: "认证", id: "register", label: "注册", method: "POST",
    path: "/api/v1/auth/register", auth: "none",
    body: { username: "alice", email: "alice@example.com", password: "correct-horse-battery" },
  },
  {
    group: "认证", id: "login", label: "登录", method: "POST",
    path: "/api/v1/auth/token", auth: "none", form: true,
    body: { username: "alice", password: "correct-horse-battery" },
  },

  // ── 用户 ──
  {
    group: "用户", id: "me", label: "当前用户", method: "GET",
    path: "/api/v1/users/me", auth: "bearer",
  },

  // ── 模型 ──
  {
    group: "模型", id: "models", label: "模型列表", method: "GET",
    path: "/api/v1/models", auth: "bearer",
  },
  {
    group: "模型", id: "model", label: "单个模型", method: "GET",
    path: "/api/v1/models/{model_name}", auth: "bearer",
    pathParams: [{ key: "model_name", example: "deepseek-chat" }],
  },

  // ── 会话 ──
  {
    group: "会话", id: "list", label: "会话列表", method: "GET",
    path: "/api/v1/conversations", auth: "bearer",
    query: [
      { key: "skip", example: "0" },
      { key: "limit", example: "20" },
      { key: "keyword", example: "" },
      { key: "archived", example: "false" },
    ],
  },
  {
    group: "会话", id: "create", label: "新建会话", method: "POST",
    path: "/api/v1/conversations", auth: "bearer",
    body: { title: "关于 RAG 的讨论" },
  },
  {
    group: "会话", id: "latest", label: "最近会话", method: "GET",
    path: "/api/v1/conversations/latest", auth: "bearer",
  },
  {
    group: "会话", id: "detail", label: "会话详情", method: "GET",
    path: "/api/v1/conversations/{conversation_id}", auth: "bearer",
    pathParams: [{ key: "conversation_id", example: "1" }],
  },
  {
    group: "会话", id: "delete", label: "删除会话", method: "DELETE",
    path: "/api/v1/conversations/{conversation_id}", auth: "bearer",
    pathParams: [{ key: "conversation_id", example: "1" }],
  },

  // ── 对话 ──
  {
    group: "对话", id: "chat", label: "发消息", method: "POST",
    path: "/api/v1/conversations/{conversation_id}/chat", auth: "bearer",
    pathParams: [{ key: "conversation_id", example: "1" }],
    body: { message: "你好", model: "deepseek-chat" },
  },

  // ── 内部（走 X-API-Key） ──
  {
    group: "内部", id: "deep-health", label: "深度健康检查", method: "GET",
    path: "/internal/health/deep", auth: "apikey",
  },
  {
    group: "内部", id: "usage", label: "用量上报", method: "POST",
    path: "/internal/usage/report", auth: "apikey",
    body: { usage: [{ model: "deepseek-chat", prompt_tokens: 12, completion_tokens: 34 }] },
  },

  // ── 演示（验错误） ──
  {
    group: "演示", id: "demo-403", label: "403 禁止", method: "GET",
    path: "/api/v1/demo/errors/forbidden", auth: "bearer",
  },
  {
    group: "演示", id: "demo-404", label: "404 不存在", method: "GET",
    path: "/api/v1/demo/errors/notfound", auth: "bearer",
  },
  {
    group: "演示", id: "demo-409", label: "409 冲突", method: "GET",
    path: "/api/v1/demo/errors/conflict", auth: "bearer",
  },
  {
    group: "演示", id: "boom", label: "500 内部错误", method: "GET",
    path: "/api/v1/demo/errors/boom", auth: "bearer",
  },
];
