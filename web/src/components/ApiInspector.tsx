import { useState } from "react";
import { request, ApiError, NetworkError } from "../api";
import { storage } from "../storage";
import { ENDPOINTS, type Endpoint } from "../endpoints";
import { JsonBlock } from "./JsonBlock";

type RespState = {
  status: number;
  elapsed: number;
  traceId?: string;
  body: unknown;
};

type ParamState = {
  pathParams: Record<string, string>;
  query: Record<string, string>;
  bodyText: string;
  apiKey: string;
  useToken: boolean;
  customToken: string;
};

export function ApiInspector() {
  const [activeId, setActiveId] = useState(ENDPOINTS[0].id);
  const [resp, setResp] = useState<RespState | null>(null);
  const [loading, setLoading] = useState(false);

  const endpoint = ENDPOINTS.find((e) => e.id === activeId) ?? ENDPOINTS[0];

  const [params, setParams] = useState<ParamState>(() => buildInitial(endpoint));

  function select(id: string) {
    setActiveId(id);
    const ep = ENDPOINTS.find((e) => e.id === id)!;
    setParams(buildInitial(ep));
    setResp(null);
  }

  function buildInitial(ep: Endpoint): ParamState {
    const pathParams: Record<string, string> = {};
    (ep.pathParams ?? []).forEach((p) => (pathParams[p.key] = p.example));
    const query: Record<string, string> = {};
    (ep.query ?? []).forEach((q) => (query[q.key] = q.example));
    return {
      pathParams,
      query,
      bodyText: ep.body != null ? JSON.stringify(ep.body, null, 2) : "",
      apiKey: "dev-api-key-change-me",
      useToken: true,
      customToken: "",
    };
  }

  function buildPath(ep: Endpoint): string {
    let path = ep.path;
    for (const p of ep.pathParams ?? []) {
      const v = params.pathParams[p.key] ?? p.example;
      path = path.replace(`{${p.key}}`, encodeURIComponent(v));
    }
    const qs: string[] = [];
    for (const q of ep.query ?? []) {
      const v = params.query[q.key] ?? "";
      if (v !== "") qs.push(`${encodeURIComponent(q.key)}=${encodeURIComponent(v)}`);
    }
    if (qs.length > 0) path += `?${qs.join("&")}`;
    return path;
  }

  function parseBody(): unknown {
    if (params.bodyText.trim() === "") return undefined;
    try {
      return JSON.parse(params.bodyText);
    } catch {
      return params.bodyText; // 非法 JSON 原样传，让后端返回 422
    }
  }

  async function send() {
    setLoading(true);
    const started = performance.now();
    const path = buildPath(endpoint);
    try {
      const token =
        endpoint.auth === "bearer" ? (params.useToken ? storage.getToken() ?? "" : params.customToken) : null;

      const body = parseBody();
      const result = await request<unknown>(path, {
        method: endpoint.method,
        body: endpoint.form ? body : body,
        form: endpoint.form,
        token,
        apiKey: endpoint.auth === "apikey" ? params.apiKey : undefined,
      });

      setResp({
        status: 200,
        elapsed: performance.now() - started,
        traceId: undefined,
        body: result,
      });
    } catch (e) {
      const elapsed = performance.now() - started;
      if (e instanceof ApiError) {
        setResp({ status: e.status, elapsed, traceId: e.traceId, body: { error: { code: e.code, message: e.message, detail: e.detail } } });
      } else if (e instanceof NetworkError) {
        setResp({ status: 0, elapsed, body: { error: { code: "NETWORK", message: e.message, detail: null } } });
      } else {
        setResp({ status: 0, elapsed, body: { error: { code: "UNKNOWN", message: String(e), detail: null } } });
      }
    } finally {
      setLoading(false);
    }
  }

  // 分组
  const groups: string[] = [];
  for (const e of ENDPOINTS) {
    if (!groups.includes(e.group)) groups.push(e.group);
  }

  return (
    <div className="inspector">
      <div className="inspector-list">
        {groups.map((g) => (
          <div key={g}>
            <div className="inspector-group">{g}</div>
            {ENDPOINTS.filter((e) => e.group === g).map((e) => (
              <button
                key={e.id}
                className={`inspector-item ${e.id === activeId ? "active" : ""}`}
                onClick={() => select(e.id)}
              >
                <span className="m">{e.method}</span>
                {e.label}
              </button>
            ))}
          </div>
        ))}
      </div>

      <div className="inspector-form">
        <h3 style={{ margin: 0 }}>{endpoint.label}</h3>
        <div className="ep-path">
          {endpoint.method} {endpoint.path}
        </div>

        {(endpoint.pathParams?.length ?? 0) > 0 && (
          <>
            <div className="field-label">路径参数</div>
            {endpoint.pathParams!.map((p) => (
              <div className="field-row" key={p.key}>
                <span className="fkey">{p.key}</span>
                <input
                  value={params.pathParams[p.key] ?? ""}
                  onChange={(e) =>
                    setParams((s) => ({ ...s, pathParams: { ...s.pathParams, [p.key]: e.target.value } }))
                  }
                />
              </div>
            ))}
          </>
        )}

        {(endpoint.query?.length ?? 0) > 0 && (
          <>
            <div className="field-label">查询参数（留空则不发送）</div>
            {endpoint.query!.map((q) => (
              <div className="field-row" key={q.key}>
                <span className="fkey">{q.key}</span>
                <input
                  value={params.query[q.key] ?? ""}
                  onChange={(e) =>
                    setParams((s) => ({ ...s, query: { ...s.query, [q.key]: e.target.value } }))
                  }
                />
              </div>
            ))}
          </>
        )}

        {endpoint.body != null && (
          <>
            <div className="field-label">
              请求体（{endpoint.form ? "表单 URLSearchParams，键值取自下方 JSON" : "JSON"}）
            </div>
            <textarea
              className="json-edit"
              value={params.bodyText}
              onChange={(e) => setParams((s) => ({ ...s, bodyText: e.target.value }))}
              spellCheck={false}
            />
          </>
        )}

        <div className="field-label">鉴权</div>
        {endpoint.auth === "bearer" && (
          <div className="inspector-actions">
            <label className="token-toggle">
              <input
                type="checkbox"
                checked={params.useToken}
                onChange={(e) => setParams((s) => ({ ...s, useToken: e.target.checked }))}
              />
              使用登录 token
            </label>
            {!params.useToken && (
              <input
                style={{ flex: 1 }}
                placeholder="自定义 token（清空可验 401）"
                value={params.customToken}
                onChange={(e) => setParams((s) => ({ ...s, customToken: e.target.value }))}
              />
            )}
          </div>
        )}
        {endpoint.auth === "apikey" && (
          <div className="field-row">
            <span className="fkey">X-API-Key</span>
            <input
              value={params.apiKey}
              onChange={(e) => setParams((s) => ({ ...s, apiKey: e.target.value }))}
            />
          </div>
        )}
        {endpoint.auth === "none" && (
          <div style={{ fontSize: 12, color: "var(--muted)" }}>无需鉴权</div>
        )}

        <div className="inspector-actions">
          <button className="primary" onClick={send} disabled={loading}>
            {loading ? "发送中…" : "发送"}
          </button>
          <button onClick={() => { setParams(buildInitial(endpoint)); setResp(null); }}>重置</button>
        </div>

        {resp && (
          <>
            <div className="resp-bar">
              <span className="code" style={{ color: resp.status === 0 ? "var(--err)" : resp.status >= 400 ? "var(--warn)" : "var(--ok)" }}>
                {resp.status === 0 ? "网络失败" : resp.status}
              </span>
              <span>{(resp.elapsed / 1000).toFixed(2)}s</span>
              {resp.traceId && <span>trace {resp.traceId}</span>}
            </div>
            <JsonBlock data={resp.body} />
          </>
        )}
      </div>
    </div>
  );
}
