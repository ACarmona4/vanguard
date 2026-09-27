import {
  Activity,
  ChevronRight,
  Box,
  Layers3,
  LayoutGrid,
  UserRound,
  LogOut,
  Moon,
  Sun,
  CircleDollarSign,
} from "lucide-react";
import { useAuth } from "../../auth/AuthProvider";
export default function AppLayout({ children, page }) {
  const auth = useAuth();
  const title = {
    dashboard: "Dashboard",
    inventory: "Inventory",
    accounts: "Accounts",
    utilization: "Utilization",
    costs: "Costs",
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
          <a
            href="#costs"
            className={page === "costs" ? "nav-active" : ""}
            aria-current={page === "costs" ? "page" : undefined}
          >
            <CircleDollarSign size={18} /> Costs
          </a>
        </nav>
        <a
          href="#accounts"
          className={`sidebar-footer ${page === "accounts" ? "accounts-active" : ""}`}
          aria-current={page === "accounts" ? "page" : undefined}
        >
          <span className="avatar">{auth.user.full_name.slice(0, 1).toUpperCase()}</span>
          <div>
            <strong>{auth.user.full_name}</strong>
            <small>{auth.user.email}</small>
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
              href="#costs"
              aria-current={page === "costs" ? "page" : undefined}
            >
              Costs
            </a>
            <a
              href="#accounts"
              aria-current={page === "accounts" ? "page" : undefined}
            >
              <UserRound size={14} /> Accounts
            </a>
          </nav>
          <div className="topbar-actions">
            <button
              className="icon-button theme-toggle"
              aria-label={`Use ${auth.user.theme === "dark" ? "light" : "dark"} mode`}
              onClick={() => auth.updateProfile({
                full_name: auth.user.full_name,
                theme: auth.user.theme === "dark" ? "light" : "dark",
              })}
            >
              {auth.user.theme === "dark" ? <Sun size={16} /> : <Moon size={16} />}
            </button>
            <button className="icon-button" aria-label="Sign out" onClick={auth.logout}>
              <LogOut size={16} />
            </button>
          </div>
        </header>
        {children}
      </div>
    </div>
  );
}
