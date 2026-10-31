"""Product defaults, not universal standards; keep tuning in one place."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Policy:
    warning_percent: float = 80
    critical_percent: float = 90
    sustained_minutes: int = 15
    max_gap_seconds: int = 600
    history_days: int = 7
    history_coverage: float = 0.8
    low_cpu_p95: float = 20
    low_cpu_peak: float = 50
    low_storage_peak: float = 20
    queue_warning_seconds: float = 300
    queue_critical_seconds: float = 900
    network_growth_factor: float = 3
    network_min_bytes_per_second: float = 1024 * 1024
    # Economic assessment: resource-level billing window and evidence needed for confidence.
    cost_lookback_days: int = 30
    cost_high_confidence_days: int = 14
    cost_medium_confidence_days: int = 7
    # Suggested impact from monthly savings, in the billing account currency.
    savings_high: float = 50
    savings_medium: float = 10


DEFAULT_POLICY = Policy()

SOURCES = [
    {"title": "AWS Compute Optimizer · history and headroom", "url": "https://docs.aws.amazon.com/compute-optimizer/latest/ug/rightsizing-preferences.html"},
    {"title": "Azure Advisor · sustained utilization", "url": "https://learn.microsoft.com/en-us/azure/advisor/advisor-how-to-performance-resize-high-usage-vm-recommendations"},
    {"title": "Google Compute Engine · rightsizing", "url": "https://docs.cloud.google.com/compute/docs/instances/apply-machine-type-recommendations-for-instances"},
    {"title": "Zabbix · CPU, memory and filesystem defaults", "url": "https://www.zabbix.com/integrations/linux"},
    {"title": "Google SRE · saturation and actionable alerts", "url": "https://sre.google/sre-book/monitoring-distributed-systems/"},
    {"title": "Cloud SQL · storage shrink constraints", "url": "https://docs.cloud.google.com/sql/docs/postgres/about-storage-shrink"},
]
