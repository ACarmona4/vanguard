import {
  Activity,
  ChevronRight,
  Box,
  Layers3,
  LayoutGrid,
  UserRound,
} from "lucide-react";
export default function AppLayout({ children, page }) {
  const title = {
    dashboard: "Dashboard",
    inventory: "Inventory",
    profile: "Profile",
    utilization: "Utilization",
  }[page];
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a href="#dashboard" className="brand">
          <span className="brand-mark">v</span>vanguard
          <span className="brand-dot">.</span>
        </a>
        <div className="workspace">
          <span className="workspace-icon">
            <Layers3 size={18} />
          </span>
          <div>
            <strong>Vanguard</strong>
            <small>Cloud management</small>
          </div>
        </div>
        <div className="nav-label">GENERAL</div>
        <nav>
          <a
            className={page === "dashboard" ? "nav-active" : ""}
            aria-current={page === "dashboard" ? "page" : undefined}
            href="#dashboard"
          >
            <LayoutGrid size={18} /> Dashboard
          </a>
          <a
            className={page === "inventory" ? "nav-active" : ""}
            aria-current={page === "inventory" ? "page" : undefined}
            href="#inventory"
          >
            <Box size={18} /> Inventory
          </a>
          <a
            href="#utilization"
            className={page === "utilization" ? "nav-active" : ""}
            aria-current={page === "utilization" ? "page" : undefined}
          >
            <Activity size={18} /> Utilization
          </a>
        </nav>
        <a
          href="#profile"
          className={`sidebar-footer ${page === "profile" ? "profile-active" : ""}`}
          aria-current={page === "profile" ? "page" : undefined}
        >
          <span className="avatar">A</span>
          <div>
            <strong>Administrator</strong>
            <small>Profile and connections</small>
          </div>
          <span className="version">v0.1</span>
        </a>
      </aside>

      <div className="main-shell">
        <header className="topbar">
          <div>
            General <ChevronRight size={13} />
            <strong>{title}</strong>
          </div>
          <nav className="mobile-nav" aria-label="Main navigation">
            <a
              href="#dashboard"
              aria-current={page === "dashboard" ? "page" : undefined}
            >
              Dashboard
            </a>
            <a
              href="#inventory"
              aria-current={page === "inventory" ? "page" : undefined}
            >
              Inventory
            </a>
            <a
              href="#utilization"
              aria-current={page === "utilization" ? "page" : undefined}
            >
              Utilization
            </a>
            <a
              href="#profile"
              aria-current={page === "profile" ? "page" : undefined}
            >
              <UserRound size={14} /> Profile
            </a>
          </nav>
        </header>
        {children}
      </div>
    </div>
  );
}
