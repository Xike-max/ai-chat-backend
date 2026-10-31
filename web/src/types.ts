// 与后端 schemas.py 逐字对应的类型定义

export type User = {
  id: number;
  username: string;
  email: string;
  is_active: boolean;
  created_at: string;
};

export type Token = {
  access_token: string;
  token_type: "bearer";
  expires_in: number;
};

export type Conversation = {
  id: number;
  title: string;
  created_at: string;
  message_count: number;
  summary?: string | null;
};

export type Message = {
  id: number;
  role: "system" | "user" | "assistant";
  content: string;
  tokens: number;
  created_at: string;
};

export type ConversationDetail = Conversation & { messages: Message[] };

export type ChatResult = {
  conversation_id: number;
  reply: string;
  model: string;
  message: Message;
};

export type ModelInfo = {
  name: string;
  display_name: string;
  provider: string;
  context_window: number;
  description: string;
};

export type Page<T> = { total: number; items: T[] };

// 统一错误契约（errors.py 的 _envelope）
export type ErrorEnvelope = {
  error: {
    code: string;
    message: string;
    detail: unknown;
  };
};

// 422 的 detail：字段错误数组
export type ValidationDetail = { field: string; message: string }[];

// 500 的 detail：trace_id 对象
export type TraceDetail = { trace_id: string };
