export const PRIORITY_LABELS = { critical: "Critical", high: "High", medium: "Medium", low: "Low" };
export const IMPACT_LABELS = { ...PRIORITY_LABELS, unknown: "Not assessed" };
export const SEVERITY_LABELS = { critical: "Critical", warning: "Warning", info: "Info" };
export const CATEGORY_LABELS = { cost: "Cost", capacity: "Capacity", performance: "Performance", reliability: "Reliability", observability: "Monitoring" };
export const STATE_LABELS = { open: "Open", detected: "Pending review", approved: "Approved", executed: "Executed", rejected: "Rejected" };
export const EVENT_LABELS = { detected: "Detected", condition_cleared: "Condition cleared", prioritized: "Priority updated", approved: "Approved", executed: "Execution recorded", rejected: "Rejected" };
