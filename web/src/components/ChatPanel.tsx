import { useEffect, useRef, useState } from "react";
import { api, ApiError, NetworkError } from "../api";
import { storage } from "../storage";
import type { ChatResult, ConversationDetail, Message } from "../types";
import { MessageBubble } from "./MessageBubble";
import { toast } from "./Toast";

type Props = {
  conversationId: number | null;
  detail: ConversationDetail | null;
  onDetailChange: (detail: ConversationDetail) => void;
  onOpenLog: (traceId?: string) => void;
  onBack: () => void;
};

type MsgMeta = { elapsed?: number; status?: number; traceId?: string };

const MAX_MSG = 4000;

export function ChatPanel({ conversationId, detail, onDetailChange, onOpenLog, onBack }: Props) {
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [sendingMsg, setSendingMsg] = useState<string | null>(null);
  const [failMsg, setFailMsg] = useState<string | null>(null);
  const [metaByMsg, setMetaByMsg] = useState<Record<number, MsgMeta>>({});
  const bottomRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const messages: Message[] = detail?.messages ?? [];

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length, sendingMsg, failMsg]);

  useEffect(() => {
    setInput("");
    setSendingMsg(null);
    setFailMsg(null);
    setMetaByMsg({});
  }, [conversationId]);

  async function send(text?: string) {
    const content = (text ?? input).trim();
    if (!content || sending || conversationId == null) return;

    setInput("");
    setSending(true);
    setSendingMsg(content);
    setFailMsg(null);

    const started = performance.now();
    try {
      const result = await api.post<ChatResult>(
        `/api/v1/conversations/${conversationId}/chat`,
        { message: content, model: storage.getModel() },
      );
      const elapsed = performance.now() - started;
      setSendingMsg(null);

      // 更新本地消息列表：追加 user + assistant 两条
      const current = detail ? [...detail.messages] : [];
      // 后端返回的 message 是 assistant 消息；user 消息需自己构造
      const userMsg: Message = {
        id: -Date.now(), // 临时 id，仅本地展示
        role: "user",
        content,
        tokens: 0,
        created_at: new Date().toISOString(),
      };
      const updated: Message[] = [...current, userMsg, result.message];
      setMetaByMsg((prev) => ({
        ...prev,
        [result.message.id]: { elapsed, status: 200 },
      }));
      onDetailChange({
        ...(detail ?? { id: conversationId, title: "", created_at: "", message_count: 0 }),
        message_count: (detail?.message_count ?? 0) + 2,
        messages: updated,
      });
    } catch (e) {
      setSendingMsg(null);
      const elapsed = performance.now() - started;
      if (e instanceof NetworkError) {
        setFailMsg(content);
        toast("无法连接服务，请确认后端已启动", "err");
      } else if (e instanceof ApiError) {
        // 502 LLM 不可用：用户消息其实已落库，提示重试
        setFailMsg(content);
        setMetaByMsg((prev) => ({ ...prev, [-Date.now()]: { elapsed, status: e.status, traceId: e.traceId } }));
        toast(e.message || `请求失败（${e.code}）`, "err");
      } else {
        setFailMsg(content);
        toast("发送失败", "err");
      }
    } finally {
      setSending(false);
    }
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  }

  const canSend = input.trim() !== "" && !sending && conversationId != null;

  // 无选中会话
  if (conversationId == null) {
    return (
      <main className="main">
        <div className="chat-empty">
          <div className="empty-icon">💬</div>
          <div>从左边选一个会话，或新建</div>
        </div>
      </main>
    );
  }

  return (
    <main className="main">
      <div className="chat-head">
        <button className="chat-back" onClick={onBack} title="返回会话列表" aria-label="返回会话列表">
          ←
        </button>
        <div className="chat-head-text">
          <div className="chat-title">{detail?.title || "新会话"}</div>
          <div className="chat-sub">
            {messages.length} 条消息
            {detail?.created_at ? ` · 创建于 ${new Date(detail.created_at).toLocaleString()}` : ""}
          </div>
        </div>
      </div>

      <div className="messages">
        {messages.map((m) => {
          const meta = metaByMsg[m.id];
          return (
            <MessageBubble
              key={m.id}
              message={m}
              elapsed={meta?.elapsed}
              status={meta?.status}
              traceId={meta?.traceId}
              onClickMeta={meta?.status != null ? () => onOpenLog(meta.traceId) : undefined}
            />
          );
        })}

        {sendingMsg != null && (
          <div className="msg-row user">
            <div className="user-text">{sendingMsg}</div>
          </div>
        )}
        {sendingMsg != null && (
          <div className="msg-row">
            <div className="assistant-text">
              <span className="typing-dots">
                <span />
                <span />
                <span />
              </span>
            </div>
          </div>
        )}

        {failMsg != null && (
          <div className="msg-row user">
            <div className="bubble-error">
              {failMsg}
              <div className="bubble-foot" onClick={() => send(failMsg)}>
                发送失败，点击重试
              </div>
            </div>
          </div>
        )}

        {messages.length > 0 && (
          <div className="messages-note">仅显示最近 50 条消息</div>
        )}
        <div ref={bottomRef} />
      </div>

      <div className="chat-input-wrap">
        <div className="chat-input-box">
          <textarea
            ref={textareaRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="今天帮你做些什么？Enter 发送 / Shift+Enter 换行"
            maxLength={MAX_MSG}
            disabled={sending}
          />
          <button className="send-btn" disabled={!canSend} onClick={() => send()} title="发送" aria-label="发送">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
              <line x1="12" y1="19" x2="12" y2="5" />
              <polyline points="5 12 12 5 19 12" />
            </svg>
          </button>
        </div>
        <div className="chat-input-meta">
          <span>{sending ? "正在思考…" : ""}</span>
          <span className={`count ${input.length > 3900 ? "warn" : ""}`}>
            {input.length}/{MAX_MSG}
          </span>
        </div>
      </div>
    </main>
  );
}
