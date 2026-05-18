import type { ReactNode } from "react";

type Props = {
  sessionId: string;
  children: ReactNode;
  activeTab: "chat" | "resources";
  onTabChange: (tab: "chat" | "resources") => void;
  onLogout: () => void;
};

export function Sidebar({ sessionId, children, activeTab, onTabChange, onLogout }: Props) {
  return (
    <aside className="sidebar">
      <div className="sidebar-header">
        <div>
          <div className="sidebar-kicker">WeRead Agent</div>
          <h2>{activeTab === "chat" ? "聊天线程" : "资源目录"}</h2>
        </div>
      </div>
      <div className="sidebar-tabs">
        <button
          className={`sidebar-tab ${activeTab === "chat" ? "is-active" : ""}`}
          onClick={() => onTabChange("chat")}
          type="button"
        >
          聊天
        </button>
        <button
          className={`sidebar-tab ${activeTab === "resources" ? "is-active" : ""}`}
          onClick={() => onTabChange("resources")}
          type="button"
        >
          资源
        </button>
      </div>
      <div className="sidebar-session">session · {sessionId.slice(0, 8)}</div>
      {children}
      <button className="logout-button" onClick={onLogout} type="button">
        退出当前 session
      </button>
    </aside>
  );
}
