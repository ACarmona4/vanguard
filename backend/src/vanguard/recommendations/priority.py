"""Explainable ranking; financial impact is assessed, never invented from CPU usage."""


def assess(finding, business_relevance="medium", economic_impact="unknown"):
    severity = {"critical": 80, "warning": 40, "info": 20}[finding["severity"]]
    kind = {"alert": 15, "recommendation": 10, "suggestion": 0}[finding["kind"]]
    relevance = {"low": 0, "medium": 5, "high": 15}[business_relevance]
    economic = {"unknown": 0, "low": 0, "medium": 10, "high": 25}[economic_impact]
    score = min(100, severity + kind + relevance + economic)
    critical = finding["severity"] == "critical" and finding["kind"] == "alert"
    cost = finding.get("cost") or {}
    savings = cost.get("savings")
    if critical:
        score = 100
    priority = (
        "critical"
        if critical
        else "high"
        if score >= 60
        else "medium"
        if score >= 35
        else "low"
    )
    return {
        "priority": priority,
        "priority_score": score,
        "priority_reason": (
            ("Critical alert. " if critical else "")
            + ("Cost impact has not been assessed. " if economic_impact == "unknown"
               else f"{economic_impact.capitalize()} cost impact. ")
            + f"{business_relevance.capitalize()} business relevance."
            + (f" Estimated savings: {savings['monthly_amount']:,.2f} {cost['currency']}/month "
               f"({savings['confidence']} confidence)." if savings else "")
        ),
    }
