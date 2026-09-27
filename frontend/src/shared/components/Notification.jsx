import { useEffect, useRef, useState } from "react";
import { CheckCircle2, CircleAlert, TriangleAlert } from "lucide-react";

const icons = {
  success: CheckCircle2,
  warning: TriangleAlert,
  error: CircleAlert,
};

export default function Notification({ message, type = "success", onDismiss }) {
  const [visible, setVisible] = useState(Boolean(message));
  const dismissRef = useRef(onDismiss);
  dismissRef.current = onDismiss;

  useEffect(() => {
    if (!message) return undefined;
    setVisible(true);
    const timer = window.setTimeout(() => {
      setVisible(false);
      dismissRef.current?.();
    }, 5000);
    return () => window.clearTimeout(timer);
  }, [message]);

  if (!message || !visible) return null;
  const Icon = icons[type] || CircleAlert;
  return (
    <div
      className={`notification ${type}`}
      role={type === "error" ? "alert" : "status"}
    >
      <Icon size={16} />
      <span>{message}</span>
    </div>
  );
}
