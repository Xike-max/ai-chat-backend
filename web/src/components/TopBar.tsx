import { useSyncExternalStore } from "react";
import { requestLog } from "../requestLog";
import { storage } from "../storage";
import { HealthBadge } from "./HealthBadge";
import { ModelPicker } from "./ModelPicker";

type Props = {
  view: "chat" | "inspector";
  onViewChange: (v: "chat" | "inspector") => void;
  onOpenLog: () => void;
  onLogout: () => void;
};

export function TopBar({ view, onViewChange, onOpenLog, onLogout }: Props) {
  const logs = useSyncExternalStore(requestLog.subscribe, requestLog.getSnapshot);
  const unread = logs.filter((l) => l.status === 0 || l.status >= 400).length;
  const username = storage.getUsername() ?? "未登录";

  return (
    <header className="topbar">
      <span className="brand">
        <span className="brand-logo">AI</span>
        AI Chat 验收台
      </span>
      <HealthBadge />
      <span className="spacer" />
      <ModelPicker />
      <button onClick={onOpenLog}>日志 {unread > 0 ? unread : ""}</button>
      <button className={view === "inspector" ? "primary" : ""} onClick={() => onViewChange("inspector")}>
        检查页
      </button>
      <button onClick={onLogout}>{username} ▾</button>
    </header>
  );
}
