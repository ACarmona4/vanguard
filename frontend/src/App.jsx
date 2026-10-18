import UtilizationPage from "./utilization/UtilizationPage";
import InventoryPage from "./inventory/InventoryPage";
import AppLayout from "./shared/layout/AppLayout";
import { useEffect, useRef, useState } from "react";
import DashboardPage from "./dashboard/DashboardPage";
import AccountSettingsPage from "./accounts/AccountSettingsPage";
import CloudAccessRequired from "./accounts/CloudAccessRequired";
import { listConnections } from "./accounts/api";
import AuthPage from "./auth/AuthPage";
import { useAuth } from "./auth/AuthProvider";
import CostsPage from "./costs/CostsPage";
import OrganizationPage from "./organizations/OrganizationPage";
import RecommendationsPage from "./recommendations/RecommendationsPage";

function currentPage() {
  const page = window.location.hash.replace("#", "").split("?")[0];
  if (page === "profile") return "accounts";
  return ["dashboard", "inventory", "utilization", "costs", "recommendations", "organization", "accounts"].includes(page)
    ? page
    : "dashboard";
}

function Workspace() {
  const auth = useAuth();
  const [page, setPage] = useState(currentPage);
  const cloudRequest = useRef(null);
  const [cloudAccess, setCloudAccess] = useState({
    loading: true,
    reason: null,
    connections: [],
  });

  async function verifyCloudAccess({ silent = false } = {}) {
    cloudRequest.current?.abort();
    const controller = new AbortController();
    cloudRequest.current = controller;
    if (!silent) setCloudAccess((current) => ({ ...current, loading: true }));
    try {
      const connections = await listConnections(controller.signal);
      if (controller.signal.aborted) return;
      const usable = connections.some(
        (connection) => connection.status !== "error",
      );
      setCloudAccess({
        loading: false,
        reason:
          connections.length === 0
            ? "not-configured"
            : usable
              ? null
              : "credentials-error",
        connections,
      });
    } catch {
      if (controller.signal.aborted) return;
      setCloudAccess({
        loading: false,
        reason: "verification-error",
        connections: [],
      });
    }
  }

  useEffect(() => {
    if (!auth.user) return undefined;
    const navigate = () => {
      setPage(currentPage());
    };
    window.addEventListener("hashchange", navigate);
    window.addEventListener("cloud-connections-changed", verifyCloudAccess);
    verifyCloudAccess();
    const timer = window.setInterval(
      () => verifyCloudAccess({ silent: true }),
      30_000,
    );
    return () => {
      window.clearInterval(timer);
      cloudRequest.current?.abort();
      window.removeEventListener("hashchange", navigate);
      window.removeEventListener(
        "cloud-connections-changed",
        verifyCloudAccess,
      );
    };
  }, [auth.user?.id]);

  const protectedPage = !["accounts", "organization", "recommendations"].includes(page);
  const blocked = protectedPage && !cloudAccess.loading && cloudAccess.reason;

  return (
    <AppLayout page={page}>
      {protectedPage && cloudAccess.loading ? (
        <main className="cloud-access-page">
          <div className="cloud-access-loading" role="status">
            Verifying cloud connections…
          </div>
        </main>
      ) : blocked ? (
        <CloudAccessRequired
          reason={cloudAccess.reason}
          connections={cloudAccess.connections}
          onRetry={verifyCloudAccess}
        />
      ) : page === "inventory" ? (
        <InventoryPage />
      ) : page === "utilization" ? (
        <UtilizationPage />
      ) : page === "costs" ? (
        <CostsPage />
      ) : page === "recommendations" ? (
        <RecommendationsPage />
      ) : page === "organization" ? (
        <OrganizationPage />
      ) : page === "accounts" ? (
        <AccountSettingsPage />
      ) : (
        <DashboardPage />
      )}
    </AppLayout>
  );
}

export default function App() {
  const auth = useAuth();
  if (auth.loading) return <main className="auth-page"><p role="status">Loading Vanguard…</p></main>;
  if (!auth.user) return <AuthPage />;
  return <Workspace key={`${auth.user.id}:${auth.user.organization_id}`} />;
}
