// 极简请求日志 store：记录每次请求，供抽屉订阅
// 用 useSyncExternalStore 订阅，getSnapshot 只在 push/clear 时换引用

export type LogEntry = {
  method: string;
  path: string;
  status: number;
  elapsed: number;
  traceId?: string;
  processTime?: string;
  reqBody?: string;
  resBody?: unknown;
  error?: string;
};

let entries: LogEntry[] = [];
const listeners = new Set<() => void>();
const MAX = 200;

export const requestLog = {
  push(entry: LogEntry) {
    entries = [entry, ...entries].slice(0, MAX); // 新的在前
    listeners.forEach((l) => l());
  },
  clear() {
    entries = [];
    listeners.forEach((l) => l());
  },
  subscribe(l: () => void) {
    listeners.add(l);
    return () => {
      listeners.delete(l);
    };
  },
  getSnapshot() {
    return entries;
  },
};
