import { useEffect, useState } from "react";

type Health = { ok: boolean; latency?: number };

export function HealthBadge() {
  const [health, setHealth] = useState<Health>({ ok: false });

  useEffect(() => {
    let alive = true;
    async function check() {
      const started = performance.now();
      try {
        const res = await fetch("/api/v1/health");
        const latency = Math.round(performance.now() - started);
        if (alive) setHealth({ ok: res.ok, latency });
      } catch {
        if (alive) setHealth({ ok: false });
      }
    }
    check();
    const timer = setInterval(check, 15000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  return (
    <div className={`health ${health.ok ? "ok" : "bad"}`}>
      <span className="dot" />
      <span>{health.ok ? `后端正常${health.latency != null ? ` ${health.latency}ms` : ""}` : "后端离线"}</span>
    </div>
  );
}
