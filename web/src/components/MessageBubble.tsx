import type { Message } from "../types";

type Props = {
  message: Message;
  elapsed?: number; // assistant 消息的耗时（ms）
  status?: number;
  traceId?: string;
  onClickMeta?: () => void;
};

// 复制按钮：复制消息内容
function copyText(text: string) {
  navigator.clipboard?.writeText(text).catch(() => {});
}

export function MessageBubble({ message, elapsed, status, traceId, onClickMeta }: Props) {
  const isUser = message.role === "user";

  // 用户消息：右侧浅色圆角块
  if (isUser) {
    return (
      <div className="msg-row user">
        <div className="user-text">{message.content}</div>
      </div>
    );
  }

  // AI 消息：平铺文本 + 底部操作条（贴近 WorkBuddy 风格）
  return (
    <div className="msg-row">
      <div style={{ flex: 1, minWidth: 0 }}>
        <div className="assistant-text">{message.content}</div>
        <div className="msg-actions">
          <button className="action-icon" title="复制" onClick={() => copyText(message.content)}>
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
              <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
            </svg>
          </button>
          <button className="action-icon" title="点赞">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3" />
            </svg>
          </button>
          <button className="action-icon" title="点踩">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M10 15v4a3 3 0 0 0 3 3l4-9V2H5.72a2 2 0 0 0-2 1.7l-1.38 9a2 2 0 0 0 2 2.3zm7-13h2.67A2.31 2.31 0 0 1 22 4v7a2.31 2.31 0 0 1-2.33 2H17" />
            </svg>
          </button>
          <button className="action-icon" title="重新生成">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="23 4 23 10 17 10" />
              <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10" />
            </svg>
          </button>
          {(elapsed != null || status != null) && (
            <span className="meta" onClick={onClickMeta} title="点击查看对应请求日志">
              {status != null ? `${status}` : ""}
              {status != null && elapsed != null ? " · " : ""}
              {elapsed != null ? `${(elapsed / 1000).toFixed(1)}s` : ""}
              {traceId ? ` · trace ${traceId.slice(0, 8)}` : ""}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}
