import { useEffect, useState } from "react";

type ToastItem = { id: number; text: string; kind: "err" | "ok" | "info" };

let toastId = 0;
const listeners = new Set<(t: ToastItem) => void>();

export function toast(text: string, kind: ToastItem["kind"] = "info") {
  const item = { id: ++toastId, text, kind };
  listeners.forEach((l) => l(item));
}

export function Toast() {
  const [items, setItems] = useState<ToastItem[]>([]);

  useEffect(() => {
    const handler = (t: ToastItem) => {
      setItems((prev) => [...prev, t]);
      setTimeout(() => setItems((prev) => prev.filter((x) => x.id !== t.id)), 4000);
    };
    listeners.add(handler);
    return () => {
      listeners.delete(handler);
    };
  }, []);

  if (items.length === 0) return null;
  return (
    <div className="toast-wrap">
      {items.map((t) => (
        <div key={t.id} className={`toast ${t.kind}`}>
          {t.text}
        </div>
      ))}
    </div>
  );
}
