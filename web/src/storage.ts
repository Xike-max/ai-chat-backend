// token / 模型选择 / 用户名的本地持久化

const TOKEN_KEY = "ac_token";
const EXPIRES_KEY = "ac_expires_at";
const USERNAME_KEY = "ac_username";
const MODEL_KEY = "ac_model";

export const storage = {
  getToken(): string | null {
    return localStorage.getItem(TOKEN_KEY);
  },
  getExpiresAt(): number | null {
    const v = localStorage.getItem(EXPIRES_KEY);
    return v ? Number(v) : null;
  },
  getUsername(): string | null {
    return localStorage.getItem(USERNAME_KEY);
  },
  getModel(): string {
    return localStorage.getItem(MODEL_KEY) || "deepseek-chat";
  },

  setSession(token: string, expiresIn: number, username: string) {
    localStorage.setItem(TOKEN_KEY, token);
    localStorage.setItem(EXPIRES_KEY, String(Date.now() + expiresIn * 1000));
    localStorage.setItem(USERNAME_KEY, username);
  },
  setModel(model: string) {
    localStorage.setItem(MODEL_KEY, model);
  },
  clear() {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(EXPIRES_KEY);
    localStorage.removeItem(USERNAME_KEY);
  },

  // 剩余秒数，留 60 秒余量；已过期或未登录返回 0
  remainingSeconds(): number {
    const exp = this.getExpiresAt();
    if (!exp) return 0;
    const remain = Math.floor((exp - 60 * 1000 - Date.now()) / 1000);
    return remain > 0 ? remain : 0;
  },
};
