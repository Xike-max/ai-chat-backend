import { useEffect, useState } from "react";
import { api, ApiError, UnauthorizedError, NetworkError } from "./api";
import { storage } from "./storage";
import type { Conversation, ConversationDetail, Page, User } from "./types";
import { TopBar } from "./components/TopBar";
import { Sidebar } from "./components/Sidebar";
import { ChatPanel } from "./components/ChatPanel";
import { LoginPage } from "./components/LoginPage";
import { NetworkDrawer } from "./components/NetworkDrawer";
import { ApiInspector } from "./components/ApiInspector";
import { Toast, toast } from "./components/Toast";

type AuthState = "booting" | "loggedIn" | "loggedOut" | "offline";

const PAGE_SIZE = 20;

export default function App() {
  const [auth, setAuth] = useState<AuthState>("booting");
  const [view, setView] = useState<"chat" | "inspector">("chat");

  // 会话列表
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [total, setTotal] = useState(0);
  const [listLoading, setListLoading] = useState(false);
  const [activeId, setActiveId] = useState<number | null>(null);
  const [detail, setDetail] = useState<ConversationDetail | null>(null);

  // 日志抽屉
  const [logOpen, setLogOpen] = useState(false);
  const [highlightTrace, setHighlightTrace] = useState<string | undefined>();

  // 启动时校验 token
  useEffect(() => {
    boot();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function boot() {
    const token = storage.getToken();
    if (!token) {
      setAuth("loggedOut");
      return;
    }
    try {
      await api.get<User>("/api/v1/users/me");
      setAuth("loggedIn");
      loadConversations();
    } catch (e) {
      if (e instanceof UnauthorizedError) {
        storage.clear();
        setAuth("loggedOut");
        toast("登录已过期，请重新登录", "err");
      } else if (e instanceof NetworkError) {
        setAuth("offline");
      } else {
        storage.clear();
        setAuth("loggedOut");
      }
    }
  }

  async function loadConversations(keyword?: string, skip = 0) {
    setListLoading(true);
    try {
      const qs = new URLSearchParams();
      qs.set("skip", String(skip));
      qs.set("limit", String(PAGE_SIZE));
      if (keyword) qs.set("keyword", keyword);
      const page = await api.get<Page<Conversation>>(`/api/v1/conversations?${qs.toString()}`);
      if (skip === 0) {
        setConversations(page.items);
      } else {
        setConversations((prev) => [...prev, ...page.items]);
      }
      setTotal(page.total);
    } catch (e) {
      handleAuthError(e);
    } finally {
      setListLoading(false);
    }
  }

  function handleAuthError(e: unknown) {
    if (e instanceof UnauthorizedError) {
      storage.clear();
      setAuth("loggedOut");
      toast("登录状态无效或已过期", "err");
    } else if (e instanceof NetworkError) {
      toast(e.message, "err");
    } else if (e instanceof ApiError) {
      toast(e.message || e.code, "err");
    }
  }

  async function selectConversation(id: number) {
    setActiveId(id);
    try {
      const d = await api.get<ConversationDetail>(`/api/v1/conversations/${id}`);
      setDetail(d);
    } catch (e) {
      handleAuthError(e);
    }
  }

  async function createConversation() {
    try {
      const c = await api.post<Conversation>("/api/v1/conversations", {
        title: "新会话",
      });
      setConversations((prev) => [c, ...prev]);
      setTotal((t) => t + 1);
      setActiveId(c.id);
      setDetail({ ...c, messages: [] });
    } catch (e) {
      handleAuthError(e);
    }
  }

  async function deleteConversation(id: number) {
    try {
      await api.del(`/api/v1/conversations/${id}`);
      const next = conversations.filter((c) => c.id !== id);
      setConversations(next);
      setTotal((t) => Math.max(0, t - 1));
      if (activeId === id) {
        if (next.length > 0) {
          selectConversation(next[0].id);
        } else {
          setActiveId(null);
          setDetail(null);
        }
      }
    } catch (e) {
      handleAuthError(e);
    }
  }

  function loadMore() {
    loadConversations(undefined, conversations.length);
  }

  function refreshList(keyword?: string) {
    loadConversations(keyword, 0);
  }

  function handleLoginSuccess() {
    setAuth("loggedIn");
    setConversations([]);
    setTotal(0);
    setActiveId(null);
    setDetail(null);
    loadConversations();
  }

  function handleLogout() {
    storage.clear();
    setAuth("loggedOut");
    setConversations([]);
    setActiveId(null);
    setDetail(null);
  }

  function openLog(traceId?: string) {
    setHighlightTrace(traceId);
    setLogOpen(true);
  }

  // 渲染
  if (auth === "booting") {
    return <div className="skeleton">正在初始化…</div>;
  }

  if (auth === "offline") {
    return (
      <div className="login-page">
        <div className="login-card">
          <h1>无法连接后端</h1>
          <div className="sub">请确认后端服务已启动（127.0.0.1:8000）</div>
          <button className="primary login-submit" onClick={() => { setAuth("booting"); boot(); }}>
            重试
          </button>
        </div>
      </div>
    );
  }

  if (auth === "loggedOut") {
    return (
      <>
        <LoginPage onSuccess={handleLoginSuccess} />
        <Toast />
      </>
    );
  }

  return (
    <div className="app">
      <TopBar
        view={view}
        onViewChange={(v) => setView(v)}
        onOpenLog={() => openLog()}
        onLogout={handleLogout}
      />

      {view === "chat" ? (
        <div className="body">
          <Sidebar
            conversations={conversations}
            total={total}
            activeId={activeId}
            loading={listLoading}
            onSelect={selectConversation}
            onCreate={createConversation}
            onDelete={deleteConversation}
            onLoadMore={loadMore}
            onRefresh={refreshList}
          />
          <ChatPanel
            conversationId={activeId}
            detail={detail}
            onDetailChange={setDetail}
            onOpenLog={openLog}
            onBack={() => {
              setActiveId(null);
              setDetail(null);
            }}
          />
        </div>
      ) : (
        <ApiInspector />
      )}

      <NetworkDrawer open={logOpen} onClose={() => setLogOpen(false)} highlightTraceId={highlightTrace} />
      <Toast />
    </div>
  );
}
