import UtilizationPage from "./utilization/UtilizationPage";
import InventoryPage from "./inventory/InventoryPage";
import AppLayout from "./shared/layout/AppLayout";
import { useEffect, useState } from "react";
import DashboardPage from "./dashboard/DashboardPage";
import ProfilePage from "./profile/ProfilePage";
import CloudAccessRequired from "./profile/CloudAccessRequired";
import { listConnections } from "./profile/api";

function currentPage() {
  const page = window.location.hash.replace("#", "");
  return ["dashboard", "inventory", "utilization", "profile"].includes(page)
    ? page
    : "dashboard";
}

export default function App() {
  const [page, setPage] = useState(currentPage);
  const [cloudAccess, setCloudAccess] = useState({
    loading: true,
    reason: null,
    connections: [],
  });

  async function verifyCloudAccess({ silent = false } = {}) {
    if (!silent) setCloudAccess((current) => ({ ...current, loading: true }));
    try {
      const connections = await listConnections();
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
      setCloudAccess({
        loading: false,
        reason: "verification-error",
        connections: [],
      });
    }
  }

  useEffect(() => {
    const navigate = () => {
      setPage(currentPage());
      verifyCloudAccess();
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
      window.removeEventListener("hashchange", navigate);
      window.removeEventListener(
        "cloud-connections-changed",
        verifyCloudAccess,
      );
    };
  }, []);

  const protectedPage = page !== "profile";
  const blocked = protectedPage && !cloudAccess.loading && cloudAccess.reason;

  return (
    <AppLayout page={page}>
      {protectedPage && cloudAccess.loading ? (
        <main className="cloud-access-page">
          <div className="cloud-access-loading" role="status">
            Verificando conexiones cloud…
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
      ) : page === "profile" ? (
        <ProfilePage />
      ) : (
        <DashboardPage />
      )}
    </AppLayout>
  );
}
