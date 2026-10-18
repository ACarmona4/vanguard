import { createContext, useContext, useEffect, useMemo, useRef, useState } from "react";
import { apiRequest, setSessionContext } from "../api/client";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [state, setState] = useState({ loading: true, user: null });
  const sessionRevision = useRef(0);

  function accept(payload) {
    sessionRevision.current += 1;
    setSessionContext(payload?.csrf_token, payload?.user?.organization_id);
    setState({ loading: false, user: payload?.user || null });
  }

  useEffect(() => {
    const controller = new AbortController();
    const revision = sessionRevision.current;
    apiRequest("auth/me", { signal: controller.signal })
      .then((payload) => { if (!controller.signal.aborted && revision === sessionRevision.current) accept(payload); })
      .catch(() => { if (!controller.signal.aborted && revision === sessionRevision.current) accept(null); });
    const expired = () => accept(null);
    window.addEventListener("auth-expired", expired);
    return () => { controller.abort(); window.removeEventListener("auth-expired", expired); };
  }, []);

  useEffect(() => {
    const theme = state.user?.theme || "light";
    document.documentElement.dataset.theme = theme;
    document.querySelector('meta[name="theme-color"]')?.setAttribute(
      "content",
      theme === "dark" ? "#17181b" : "#ffffff",
    );
  }, [state.user?.theme]);

  const value = useMemo(
    () => ({
      ...state,
      async login(payload) {
        const result = await apiRequest("auth/login", {
          method: "POST",
          body: JSON.stringify(payload),
        });
        accept(result);
      },
      async signup(payload) {
        const result = await apiRequest("auth/signup", {
          method: "POST",
          body: JSON.stringify(payload),
        });
        accept(result);
      },
      async switchOrganization(id) {
        accept(await apiRequest(`organizations/${id}/switch`, { method: "POST" }));
      },
      async logout() {
        const revision = sessionRevision.current;
        try {
          await apiRequest("auth/logout", { method: "POST" });
        } finally {
          if (revision === sessionRevision.current) accept(null);
        }
      },
      async updateProfile(payload) {
        const revision = sessionRevision.current;
        const user = await apiRequest("account/profile", {
          method: "PUT",
          body: JSON.stringify(payload),
        });
        if (revision === sessionRevision.current) setState({ loading: false, user });
        return user;
      },
    }),
    [state],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  return useContext(AuthContext);
}
