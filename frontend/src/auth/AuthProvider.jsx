import { createContext, useContext, useEffect, useMemo, useState } from "react";
import { apiRequest, setCsrfToken } from "../api/client";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [state, setState] = useState({ loading: true, user: null });

  function accept(payload) {
    setCsrfToken(payload?.csrf_token);
    setState({ loading: false, user: payload?.user || null });
  }

  useEffect(() => {
    apiRequest("auth/me")
      .then(accept)
      .catch(() => accept(null));
    const expired = () => accept(null);
    window.addEventListener("auth-expired", expired);
    return () => window.removeEventListener("auth-expired", expired);
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
      async logout() {
        try {
          await apiRequest("auth/logout", { method: "POST" });
        } finally {
          accept(null);
        }
      },
      async updateProfile(payload) {
        const user = await apiRequest("account/profile", {
          method: "PUT",
          body: JSON.stringify(payload),
        });
        setState({ loading: false, user });
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
