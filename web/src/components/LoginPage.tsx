import { useState } from "react";
import { api, ApiError } from "../api";
import { storage } from "../storage";
import type { Token, User } from "../types";

type Mode = "login" | "register";

export function LoginPage({ onSuccess }: { onSuccess: () => void }) {
  const [mode, setMode] = useState<Mode>("login");
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function submit() {
    setError("");
    setLoading(true);
    try {
      if (mode === "register") {
        await api.post<User>("/api/v1/auth/register", {
          username,
          email,
          password,
        });
        // 注册成功后自动登录
        const token = await api.post<Token>(
          "/api/v1/auth/token",
          { username, password },
          { form: true },
        );
        storage.setSession(token.access_token, token.expires_in, username);
        onSuccess();
      } else {
        const token = await api.post<Token>(
          "/api/v1/auth/token",
          { username, password },
          { form: true },
        );
        storage.setSession(token.access_token, token.expires_in, username);
        onSuccess();
      }
    } catch (e) {
      setError(extractMessage(e));
    } finally {
      setLoading(false);
    }
  }

  function extractMessage(e: unknown): string {
    if (e instanceof ApiError) {
      // 优先用后端的 message；422 时用 detail 数组拼接
      const detail = e.detail;
      if (Array.isArray(detail) && detail.length > 0) {
        return detail.map((d) => d.message).join("；");
      }
      return e.message || e.code;
    }
    return "无法连接服务，请确认后端已启动";
  }

  const canSubmit =
    !loading &&
    (mode === "register"
      ? username.trim() !== "" && email.trim() !== "" && password !== ""
      : username.trim() !== "" && password !== "");

  function handleEnter(e: React.KeyboardEvent) {
    if (e.key === "Enter" && canSubmit) submit();
  }

  return (
    <div className="login-page">
      <div className="login-card">
        <h1>AI Chat 验收台</h1>
        <div className="sub">登录后逐个验证后端接口能力</div>

        <div className="login-tabs">
          <button className={mode === "login" ? "active" : ""} onClick={() => { setMode("login"); setError(""); }}>
            登录
          </button>
          <button className={mode === "register" ? "active" : ""} onClick={() => { setMode("register"); setError(""); }}>
            注册
          </button>
        </div>

        <div className="form-field">
          <label>用户名或邮箱</label>
          <input
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            onKeyDown={handleEnter}
            placeholder="alice 或 alice@example.com"
            autoFocus
          />
        </div>

        {mode === "register" && (
          <div className="form-field">
            <label>邮箱</label>
            <input
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              onKeyDown={handleEnter}
              placeholder="alice@example.com"
            />
          </div>
        )}

        <div className="form-field">
          <label>密码</label>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            onKeyDown={handleEnter}
            placeholder="8–72 字节"
          />
        </div>

        <div className="form-error">{error}</div>

        <button className="primary login-submit" disabled={!canSubmit} onClick={submit}>
          {loading ? "处理中…" : mode === "login" ? "登录" : "注册并登录"}
        </button>

        <div className="form-hint">
          {mode === "login"
            ? "密码错误会提示「用户名或密码错误」，不区分账号是否存在"
            : "密码按字节校验，长度须在 8–72 字节之间"}
        </div>
      </div>
    </div>
  );
}
