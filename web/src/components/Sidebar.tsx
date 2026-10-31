import { useEffect, useRef, useState } from "react";
import { storage } from "../storage";
import type { Conversation } from "../types";

type Props = {
  conversations: Conversation[];
  total: number;
  activeId: number | null;
  loading: boolean;
  onSelect: (id: number) => void;
  onCreate: () => Promise<void>;
  onDelete: (id: number) => Promise<void>;
  onLoadMore: () => void;
  onRefresh: (keyword?: string) => void;
};

function fmtDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function useCountdown() {
  const [remaining, setRemaining] = useState(storage.remainingSeconds());
  useEffect(() => {
    const timer = setInterval(() => setRemaining(storage.remainingSeconds()), 60 * 1000);
    return () => clearInterval(timer);
  }, []);
  return remaining;
}

function fmtRemain(sec: number): string {
  if (sec <= 0) return "已过期";
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  if (m >= 60) {
    const h = Math.floor(m / 60);
    return `剩余 ${h} 小时 ${m % 60} 分钟`;
  }
  return `剩余 ${m} 分钟 ${s} 秒`;
}

export function Sidebar({
  conversations,
  total,
  activeId,
  loading,
  onSelect,
  onCreate,
  onDelete,
  onLoadMore,
  onRefresh,
}: Props) {
  const [keyword, setKeyword] = useState("");
  const [creating, setCreating] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const remaining = useCountdown();
  const username = storage.getUsername() ?? "未登录";

  // 搜索防抖 300ms；空串不传 keyword（后端 min_length=1）
  useEffect(() => {
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => {
      onRefresh(keyword.trim() === "" ? undefined : keyword.trim());
    }, 300);
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [keyword]);

  async function handleCreate() {
    setCreating(true);
    try {
      await onCreate();
    } finally {
      setCreating(false);
    }
  }

  async function handleDelete(id: number, e: React.MouseEvent) {
    e.stopPropagation();
    if (!window.confirm("确定删除这个会话吗？删除后不可恢复。")) return;
    await onDelete(id);
  }

  const hasMore = conversations.length < total;
  const showEmpty = !loading && conversations.length === 0;

  return (
    <aside className="sidebar">
      <div className="sidebar-head">
        <button className="primary" onClick={handleCreate} disabled={creating}>
          {creating ? "创建中…" : "+ 新建会话"}
        </button>
      </div>

      <div className="sidebar-search">
        <input
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
          placeholder="🔍 搜索会话"
        />
      </div>

      <div className="sidebar-list">
        {loading && <div className="conv-empty">加载中…</div>}

        {showEmpty && (
          <div className="conv-empty">
            {keyword.trim() === "" ? "还没有会话，点上面新建" : "没找到匹配的会话"}
          </div>
        )}

        {conversations.map((c) => (
          <div
            key={c.id}
            className={`conv-item ${c.id === activeId ? "active" : ""}`}
            onClick={() => onSelect(c.id)}
          >
            <div className="conv-main">
              <div className="conv-title">{c.title || "未命名会话"}</div>
              <div className="conv-meta">
                {c.message_count} 条 · {fmtDate(c.created_at)}
              </div>
            </div>
            <button className="conv-del" title="删除" onClick={(e) => handleDelete(c.id, e)}>
              🗑
            </button>
          </div>
        ))}
      </div>

      {hasMore && (
        <div className="sidebar-loadmore">
          <button onClick={onLoadMore}>加载更多</button>
        </div>
      )}

      <div className="sidebar-foot">
        <div className="avatar">{username.charAt(0).toUpperCase()}</div>
        <div className="foot-info">
          <div className="foot-user">{username}</div>
          <div className={`foot-expire ${remaining < 5 * 60 ? "warn" : ""}`}>
            {remaining > 0 ? fmtRemain(remaining) : "token 已过期"}
          </div>
        </div>
      </div>
    </aside>
  );
}
