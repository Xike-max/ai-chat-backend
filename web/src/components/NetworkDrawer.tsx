import { useMemo, useState, useSyncExternalStore } from "react";
import { requestLog, type LogEntry } from "../requestLog";

type Props = {
  open: boolean;
  onClose: () => void;
  highlightTraceId?: string;
};

function statusClass(status: number): string {
  if (status === 0) return "s0";
  if (status >= 500) return "s5";
  if (status >= 400) return "s4";
  return "s2";
}

function maskAuth(reqBody: string): string {
  // Authorization 不会出现在 reqBody 里（它是 header），这里保留以防万一
  return reqBody.replace(/(Authorization["':\s]*[=:]?\s*Bearer\s+)[A-Za-z0-9._-]+/g, "$1eyJhbGciOi...");
}

function entryToText(entry: LogEntry): string {
  const lines = [
    `${entry.method} ${entry.path} → ${entry.status}${entry.status === 0 ? " (网络失败)" : ""} (${(entry.elapsed / 1000).toFixed(2)}s)${entry.traceId ? ` trace=${entry.traceId.slice(0, 8)}` : ""}`,
  ];
  if (entry.reqBody) lines.push(`req: ${maskAuth(entry.reqBody)}`);
  if (entry.resBody != null) {
    lines.push(`res: ${typeof entry.resBody === "string" ? entry.resBody : JSON.stringify(entry.resBody)}`);
  }
  if (entry.error) lines.push(`error: ${entry.error}`);
  return lines.join("\n");
}

export function NetworkDrawer({ open, onClose, highlightTraceId }: Props) {
  const logs = useSyncExternalStore(requestLog.subscribe, requestLog.getSnapshot);
  const [filter, setFilter] = useState<"all" | "err">("all");
  const [expanded, setExpanded] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);

  const visible = useMemo(() => {
    if (filter === "all") return logs;
    return logs.filter((l) => l.status === 0 || l.status >= 400);
  }, [logs, filter]);

  function keyOf(entry: LogEntry, idx: number): string {
    return `${idx}-${entry.method}-${entry.path}-${entry.elapsed}`;
  }

  async function copyTrace(traceId: string) {
    try {
      await navigator.clipboard.writeText(traceId);
      setCopied(traceId);
      setTimeout(() => setCopied(null), 1200);
    } catch {
      /* ignore */
    }
  }

  async function copyEntry(entry: LogEntry) {
    try {
      await navigator.clipboard.writeText(entryToText(entry));
    } catch {
      /* ignore */
    }
  }

  return (
    <>
      {open && <div className="drawer-mask" onClick={onClose} />}
      <div className={`drawer ${open ? "open" : ""}`}>
        <div className="drawer-head">
          <span className="title">请求日志</span>
          <button onClick={() => requestLog.clear()}>清空</button>
          <button onClick={onClose}>× 关闭</button>
        </div>

        <div className="drawer-filter">
          <button className={filter === "all" ? "active" : ""} onClick={() => setFilter("all")}>
            全部
          </button>
          <button className={filter === "err" ? "active" : ""} onClick={() => setFilter("err")}>
            仅 4xx-5xx
          </button>
        </div>

        <div className="drawer-body">
          {visible.length === 0 && (
            <div className="conv-empty">还没有请求记录</div>
          )}
          {visible.map((entry, idx) => {
            const key = keyOf(entry, idx);
            const isOpen = expanded === key;
            const highlighted = highlightTraceId && entry.traceId === highlightTraceId;
            return (
              <div className="log-item" key={key} style={highlighted ? { background: "#fffbe6" } : undefined}>
                <div className="log-item-head" onClick={() => setExpanded(isOpen ? null : key)}>
                  <span className={`log-status ${statusClass(entry.status)}`}>
                    {entry.status === 0 ? "ERR" : entry.status}
                  </span>
                  <span className="log-method">{entry.method}</span>
                  <span className="log-path">{entry.path}</span>
                  <span className="log-time">{(entry.elapsed / 1000).toFixed(1)}s</span>
                  <span className="log-caret">{isOpen ? "▾" : "▸"}</span>
                </div>

                {isOpen && (
                  <div className="log-item-body">
                    {entry.traceId && (
                      <div className="log-kv">
                        <span className="k">trace</span>
                        <span className="v">{entry.traceId}</span>
                        <button className="log-copy-btn" onClick={() => copyTrace(entry.traceId!)}>
                          {copied === entry.traceId ? "已复制" : "复制"}
                        </button>
                      </div>
                    )}
                    {entry.processTime && (
                      <div className="log-kv">
                        <span className="k">X-Process-Time</span>
                        <span className="v">{entry.processTime}</span>
                      </div>
                    )}
                    <div className="log-kv">
                      <span className="k">耗时</span>
                      <span className="v">{(entry.elapsed / 1000).toFixed(2)}s</span>
                    </div>

                    <div className="log-section">── 请求 ──</div>
                    <div className="log-kv">
                      <span className="k">Authorization</span>
                      <span className="v">Bearer eyJhbGciOi...（脱敏）</span>
                    </div>
                    {entry.reqBody && <pre className="log-pre">{maskAuth(entry.reqBody)}</pre>}

                    <div className="log-section">── 响应 ──</div>
                    <pre className="log-pre">
                      {entry.resBody == null
                        ? entry.error
                          ? `网络错误：${entry.error}`
                          : "（无响应体）"
                        : typeof entry.resBody === "string"
                          ? entry.resBody
                          : JSON.stringify(entry.resBody, null, 2)}
                    </pre>

                    <button className="log-copy-btn" style={{ marginTop: 6 }} onClick={() => copyEntry(entry)}>
                      复制为纯文本
                    </button>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </>
  );
}
