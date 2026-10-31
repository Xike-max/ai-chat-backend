// 请求、错误、日志三合一。这是唯一碰 fetch 的地方。
import { storage } from "./storage";
import { requestLog } from "./requestLog";

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    public detail: unknown,
    public traceId?: string,
    public elapsedMs?: number,
    public message: string = "",
  ) {
    super(message || code);
    this.name = "ApiError";
  }
}

export class UnauthorizedError extends ApiError {
  constructor(status: number, code: string, detail: unknown, traceId?: string, elapsedMs?: number, message = "") {
    super(status, code, detail, traceId, elapsedMs, message);
    this.name = "UnauthorizedError";
  }
}

export class NetworkError extends Error {
  constructor(message = "无法连接服务，请确认后端已启动") {
    super(message);
    this.name = "NetworkError";
  }
}

export type RequestOptions = {
  method?: string;
  body?: unknown; // 对象或 URLSearchParams
  headers?: Record<string, string>;
  form?: boolean; // true → 把 body 对象编码成 URLSearchParams
  // 检查页验 401 时，显式传 token 覆盖（可为空串以去掉 Authorization）
  token?: string | null;
  apiKey?: string; // 内部接口走 X-API-Key
};

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const started = performance.now();
  const method = options.method ?? "GET";
  const token = options.token !== undefined ? options.token : storage.getToken();

  // 构造请求体
  let reqBodyStr = "";
  let bodyInit: BodyInit | undefined;
  if (options.form && options.body && typeof options.body === "object") {
    const params = new URLSearchParams();
    for (const [k, v] of Object.entries(options.body as Record<string, string>)) {
      params.set(k, v);
    }
    bodyInit = params;
    reqBodyStr = params.toString();
  } else if (options.body !== undefined) {
    reqBodyStr = JSON.stringify(options.body);
    bodyInit = reqBodyStr;
  }

  const isForm = options.form === true;
  const headers: Record<string, string> = {
    ...(isForm ? {} : { "Content-Type": "application/json" }),
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...(options.apiKey ? { "X-API-Key": options.apiKey } : {}),
    ...(options.headers ?? {}),
  };
  // 检查页验 401 时把 Authorization 显式设为空串
  if (headers.Authorization === "") delete headers.Authorization;

  let res: Response;
  try {
    res = await fetch(path, { method, headers, body: bodyInit });
  } catch {
    requestLog.push({
      method,
      path,
      status: 0,
      elapsed: performance.now() - started,
      error: "NETWORK",
      reqBody: reqBodyStr,
    });
    throw new NetworkError();
  }

  const elapsed = performance.now() - started;
  const traceId = res.headers.get("X-Trace-Id") ?? undefined;
  const processTime = res.headers.get("X-Process-Time") ?? undefined;

  if (res.status === 204) {
    requestLog.push({ method, path, status: 204, elapsed, traceId, processTime, reqBody: reqBodyStr });
    return undefined as T;
  }

  const text = await res.text();
  let body: unknown = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = text;
  }

  requestLog.push({
    method,
    path,
    status: res.status,
    elapsed,
    traceId,
    processTime,
    reqBody: reqBodyStr,
    resBody: body,
  });

  if (!res.ok) {
    const err = (body as { error?: { code: string; message: string; detail: unknown } })?.error ?? {
      code: `HTTP_${res.status}`,
      message: `请求失败（${res.status}）`,
      detail: null,
    };
    if (res.status === 401) {
      throw new UnauthorizedError(401, err.code, err.detail, traceId, elapsed, err.message);
    }
    throw new ApiError(res.status, err.code, err.detail, traceId, elapsed, err.message);
  }
  return body as T;
}

// 便捷方法
export const api = {
  get<T>(path: string, options: Omit<RequestOptions, "method" | "body"> = {}) {
    return request<T>(path, { ...options, method: "GET" });
  },
  post<T>(path: string, body?: unknown, options: Omit<RequestOptions, "method" | "body"> = {}) {
    return request<T>(path, { ...options, method: "POST", body });
  },
  del<T = undefined>(path: string, options: Omit<RequestOptions, "method" | "body"> = {}) {
    return request<T>(path, { ...options, method: "DELETE" });
  },
};

export { request };
