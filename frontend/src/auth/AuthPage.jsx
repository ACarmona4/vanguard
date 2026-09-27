import { useCallback, useState } from "react";
import { ArrowLeft, KeyRound, Layers3, LoaderCircle, ShieldCheck } from "lucide-react";
import { apiRequest } from "../api/client";
import Notification from "../shared/components/Notification";
import { useAuth } from "./AuthProvider";

function initialMode() {
  if (window.location.hash.startsWith("#reset/")) return "reset";
  return "login";
}

export default function AuthPage() {
  const auth = useAuth();
  const [mode, setMode] = useState(initialMode);
  const [values, setValues] = useState({ full_name: "", email: "", password: "", confirm: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const dismissError = useCallback(() => setError(""), []);
  const dismissNotice = useCallback(() => setNotice(""), []);

  function change(event) {
    setValues((current) => ({ ...current, [event.target.name]: event.target.value }));
  }

  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setNotice("");
    try {
      if (mode === "forgot") {
        const result = await apiRequest("auth/forgot-password", {
          method: "POST",
          body: JSON.stringify({ email: values.email }),
        });
        setNotice(result.message);
      } else if (mode === "reset") {
        if (values.password !== values.confirm) throw new Error("Passwords do not match.");
        const token = decodeURIComponent(window.location.hash.slice("#reset/".length));
        await apiRequest("auth/reset-password", {
          method: "POST",
          body: JSON.stringify({ token, password: values.password }),
        });
        window.location.hash = "";
        setMode("login");
        setNotice("Password updated. You can now sign in.");
      } else if (mode === "signup") {
        if (values.password !== values.confirm) throw new Error("Passwords do not match.");
        await auth.signup({ full_name: values.full_name, email: values.email, password: values.password });
      } else {
        await auth.login({ email: values.email, password: values.password });
      }
    } catch (failure) {
      setError(failure.message);
    } finally {
      setBusy(false);
    }
  }

  const title = mode === "signup" ? "Create your account" : mode === "forgot" ? "Reset your password" : mode === "reset" ? "Choose a new password" : "Welcome back";
  const subtitle = mode === "signup" ? "Your cloud inventory stays isolated from every other account." : mode === "forgot" ? "We will send a time-limited reset link if the account exists." : mode === "reset" ? "Use at least 10 characters." : "Sign in to manage your own infrastructure.";

  return (
    <main className="auth-page">
      <section className="auth-card">
        <a className="brand auth-brand" href="#"><span className="brand-mark">v</span>vanguard<span className="brand-dot">.</span></a>
        <div className="auth-icon">{mode === "forgot" || mode === "reset" ? <KeyRound size={23} /> : <Layers3 size={23} />}</div>
        <h1>{title}</h1>
        <p>{subtitle}</p>
        <form onSubmit={submit} className="auth-form">
          {mode === "signup" && <label>Full name<input required name="full_name" autoComplete="name" value={values.full_name} onChange={change} /></label>}
          {mode !== "reset" && <label>Email<input required type="email" name="email" autoComplete="email" value={values.email} onChange={change} /></label>}
          {!["forgot"].includes(mode) && <label>Password<input required minLength="10" type="password" name="password" autoComplete={mode === "login" ? "current-password" : "new-password"} value={values.password} onChange={change} /></label>}
          {["signup", "reset"].includes(mode) && <label>Confirm password<input required minLength="10" type="password" name="confirm" autoComplete="new-password" value={values.confirm} onChange={change} /></label>}
          <Notification message={error} type="error" onDismiss={dismissError} />
          <Notification message={notice} onDismiss={dismissNotice} />
          <button className="button primary full" disabled={busy}>{busy ? <><LoaderCircle className="spin" size={15} /> Please wait…</> : mode === "signup" ? "Create account" : mode === "forgot" ? "Send reset link" : mode === "reset" ? "Update password" : "Sign in"}</button>
        </form>
        <div className="auth-links">
          {mode === "login" ? <><button onClick={() => setMode("forgot")}>Forgot password?</button><button onClick={() => setMode("signup")}>Create account</button></> : <button onClick={() => { setMode("login"); setError(""); }}><ArrowLeft size={13} /> Back to sign in</button>}
        </div>
        <small className="auth-security"><ShieldCheck size={13} /> Secure sessions · isolated account data</small>
      </section>
    </main>
  );
}
