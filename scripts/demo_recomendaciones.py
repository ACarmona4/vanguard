"""Demo del motor de recomendaciones de Vanguard con datos inventados.

Ejecuta el motor real (vanguard.recommendations.engine.evaluate) sobre recursos de AWS
y GCP con métricas sintéticas, sin nube ni Prometheus, e imprime lo que recomienda.

Cada escenario trae TODAS las métricas que Vanguard conoce para ese tipo de recurso
(recientes cada 5 min + 7 días de historial por hora), salvo los escenarios que
prueban a propósito datos faltantes o historial insuficiente. Cada escenario declara
qué reglas espera y el script marca ✔ / ✘ si el motor coincide.

Uso, desde la raíz del repo:
    PYTHONPATH=backend/src .venv/bin/python scripts/demo_recomendaciones.py
    PYTHONPATH=backend/src .venv/bin/python scripts/demo_recomendaciones.py --grupo AWS
    PYTHONPATH=backend/src .venv/bin/python scripts/demo_recomendaciones.py --escenario aws_ec2_ociosa
    PYTHONPATH=backend/src .venv/bin/python scripts/demo_recomendaciones.py --lista
"""

import argparse
import random
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from vanguard.recommendations.economics import assess as assess_costs
from vanguard.recommendations.economics import suggested_impact
from vanguard.recommendations.engine import evaluate
from vanguard.utilization.catalog import IDENTITY_LABELS, metrics_for

NOW = 1_800_000_000 // 3600 * 3600
RECENT_STEP, HISTORY_STEP = 300, 3600   # recientes cada 5 min, historial cada hora
RECENT_POINTS, WEEK = 6, 168            # 30 min recientes, 7 días de historial
MIB = 1024 * 1024

# Valores "sanos" por métrica: lo que tendría un recurso normal funcionando.
HEALTHY = {
    "cpu_percent": 35, "memory_percent": 55, "filesystem_percent": 45, "connections": 40,
    "network_in_bytes_per_second": 0.4 * MIB, "network_out_bytes_per_second": 0.3 * MIB,
    "disk_read_bytes_per_second": 0.2 * MIB, "disk_write_bytes_per_second": 0.1 * MIB,
    "disk_queue": 0.5, "read_units_per_second": 12, "write_units_per_second": 4,
    "queued_messages": 20, "oldest_message_seconds": 15, "retained_bytes": 2 * MIB,
    "published_per_second": 3, "failed_per_second": 0,
}


def resource(rid, name, provider, rtype, status="running", **extra):
    attributes = {"database_version": "POSTGRES_15"} if rtype == "cloudsql_instance" else {}
    return {"id": rid, "owner_id": "demo", "connection_id": f"demo-{provider}", "provider": provider, "scope_id": f"demo-{provider}",
            "region": "us-east-1" if provider == "aws" else "us-central1",
            "resource_type": rtype, "resource_id": f"{rtype}-{rid}", "name": name, "status": status,
            "attributes": attributes, "raw_data": {}, "last_seen_at": datetime.fromtimestamp(NOW, timezone.utc),
            **extra}


def series(item, key, values, step):
    stamps = [NOW - (len(values) - i - 1) * step for i in range(len(values))]
    labels = {label: str(item.get(label) or "") for label in IDENTITY_LABELS}
    return {"metric": {**labels, "__name__": "vanguard_" + key},
            "values": [[t, str(round(v, 3))] for t, v in zip(stamps, values)]}


def wobble(base, n, rng, spread=0.05):
    """Valores alrededor de `base` con ±5 % de ruido."""
    return [max(0.0, base * (1 + rng.uniform(-spread, spread))) for _ in range(n)]


def build(item, recent=None, history=None, *, skip_recent=(), history_hours=WEEK, no_history=False, seed=1):
    """Genera todas las métricas del catálogo para `item`.

    recent/history: {métrica: valor_base o lista de valores} para sobrescribir los valores sanos.
      Si solo se da `recent` con un número, el historial usa ese mismo valor base.
    skip_recent: métricas que NO deben tener lecturas recientes (simula datos faltantes).
    """
    rng = random.Random(seed)
    recent, history = recent or {}, history or {}
    recent_series, history_series = [], []
    for definition in metrics_for(item):
        key = definition.key
        base = HEALTHY.get(key, 1)
        if key not in skip_recent:
            value = recent.get(key, base)
            values = value if isinstance(value, list) else wobble(value, RECENT_POINTS, rng)
            recent_series.append(series(item, key, values, RECENT_STEP))
        if not no_history:
            fallback = recent.get(key, base)
            value = history.get(key, base if isinstance(fallback, list) else fallback)
            values = value if isinstance(value, list) else wobble(value, history_hours, rng)
            history_series.append(series(item, key, values, HISTORY_STEP))
    return recent_series, history_series


TODAY = date.fromtimestamp(NOW)
GCP_BILLED = {"compute_instance": "zones/us-central1-a/instances", "persistent_disk": "zones/us-central1-a/disk",
              "ip_address": "regions/us-central1/addresses"}
# Billing sources of the demo connections. `demo-aws-academy` has no resource-level data, like AWS Academy.
COST_SOURCES = [{"connection_id": "demo-gcp", "provider": "gcp", "status": "success", "detail_available": True},
                {"connection_id": "demo-aws", "provider": "aws", "status": "success", "detail_available": True},
                {"connection_id": "demo-aws-academy", "provider": "aws", "status": "success", "detail_available": False}]


def billing(item, days, daily, currency="USD"):
    """Invented resource-level billing lines, in the format each provider exports."""
    billed = (f"//compute.googleapis.com/projects/123456789/{GCP_BILLED[item['resource_type']]}/{item['resource_id']}"
              if item["provider"] == "gcp" else item["resource_id"])
    return [{"owner": "demo", "connection_id": item["connection_id"], "provider": item["provider"], "resource_id": billed,
             "usage_date": TODAY - timedelta(days=day), "currency": currency, "amount": Decimal(str(daily))}
            for day in range(days)]


class _BillingTable:
    """Stands in for PostgreSQL: returns the invented lines to the real economic assessment."""

    def __init__(self, lines):
        self.lines = lines

    def execute(self, query, params):
        if "FROM cost_records" in query:
            _, start, end = params
            rows = [line for line in self.lines if start <= line["usage_date"] < end]
        else:
            rows = COST_SOURCES
        return type("Rows", (), {"fetchall": lambda _: rows})()


def scenarios():
    s = []

    def add(group, name, description, item, data, expected, **options):
        recent, history = data
        s.append({"group": group, "name": name, "description": description, "item": item,
                  "recent": recent, "history": history, "expected": set(expected), **options})

    # ───────────────────────── AWS ─────────────────────────
    g = "AWS"
    vm = resource(1, "web-sana", "aws", "ec2_instance")
    add(g, "aws_ec2_sana", "EC2 normal: CPU ~35 %, red y disco moderados, semana completa", vm,
        build(vm), {"capacity_visibility"})

    vm = resource(2, "web-ociosa", "aws", "ec2_instance", resource_id="i-0demo000002")
    add(g, "aws_ec2_ociosa", "EC2 con CPU ~6 % toda la semana → candidata a instancia más pequeña", vm,
        build(vm, {"cpu_percent": 6}), {"cpu_percent_oversized", "capacity_visibility"},
        billing=billing(vm, 20, 0.35), expected_cost=("attributed", None))

    vm = resource(3, "batch-con-pico", "aws", "ec2_instance")
    hist = wobble(6, WEEK, random.Random(3))
    hist[100] = 92  # job semanal pesado
    add(g, "aws_ec2_ociosa_con_pico", "CPU ~6 % pero un pico semanal de 92 % → NO debe recomendar reducir", vm,
        build(vm, {"cpu_percent": 6}, {"cpu_percent": hist}), {"capacity_visibility"})

    vm = resource(4, "checkout-warning", "aws", "ec2_instance")
    add(g, "aws_ec2_cpu_alta", "EC2 con CPU ~84 % sostenida 30 min → alerta warning", vm,
        build(vm, {"cpu_percent": [83, 85, 84, 86, 84, 85]}), {"cpu_percent_high", "capacity_visibility"})

    vm = resource(5, "checkout-critico", "aws", "ec2_instance")
    add(g, "aws_ec2_cpu_critica", "EC2 con CPU >90 % sostenida → alerta crítica", vm,
        build(vm, {"cpu_percent": [93, 95, 96, 94, 97, 95]}), {"cpu_percent_high", "capacity_visibility"})

    vm = resource(6, "checkout-pico", "aws", "ec2_instance")
    add(g, "aws_ec2_pico_suelto", "EC2 con un único pico de 97 % → NO debe alertar", vm,
        build(vm, {"cpu_percent": [30, 32, 97, 31, 33, 30]}), {"capacity_visibility"})

    vm = resource(7, "api-trafico", "aws", "ec2_instance")
    add(g, "aws_ec2_trafico_red", "EC2 enviando 6 MiB/s cuando lo normal es 0,3 MiB/s → crecimiento de red", vm,
        build(vm, {"network_out_bytes_per_second": 6 * MIB}, {"network_out_bytes_per_second": 0.3 * MIB}),
        {"network_out_bytes_per_second_growth", "capacity_visibility"})

    vol = resource(8, "vol-datos", "aws", "ebs_volume", status="in-use", raw_data={"Attachments": [{"InstanceId": "i-1"}]})
    add(g, "aws_ebs_sano", "Volumen EBS adjunto y con I/O normal", vol, build(vol), set())

    vol = resource(9, "vol-huerfano", "aws", "ebs_volume", status="available", raw_data={"Attachments": [], "Size": 50},
                   resource_id="vol-0demo000009", connection_id="demo-aws-academy")
    add(g, "aws_ebs_suelto", "Volumen EBS sin adjuntar en una cuenta sin costo por recurso (como AWS Academy)", vol,
        build(vol), {"unattached_volume"}, billing=[], expected_cost=("not_attributable", None))

    t = resource(10, "sessions", "aws", "dynamodb_table", status="ACTIVE")
    add(g, "aws_dynamodb_sana", "Tabla DynamoDB con lecturas/escrituras normales", t, build(t), set())

    q = resource(11, "pedidos-queue", "aws", "sqs_queue", status=None)
    add(g, "aws_sqs_sana", "Cola SQS procesando al día (mensaje más viejo ~15 s)", q, build(q), set())

    q = resource(12, "pagos-lenta", "aws", "sqs_queue", status=None)
    add(g, "aws_sqs_lenta", "SQS con mensajes esperando ~6 min → warning", q,
        build(q, {"oldest_message_seconds": 380, "queued_messages": 900}), {"oldest_message_seconds_high"})

    q = resource(13, "pagos-atascada", "aws", "sqs_queue", status=None)
    add(g, "aws_sqs_atascada", "SQS con mensajes esperando ~16 min → crítica", q,
        build(q, {"oldest_message_seconds": 980, "queued_messages": 4200}), {"oldest_message_seconds_high"})

    t = resource(14, "notificaciones", "aws", "sns_topic", status=None)
    add(g, "aws_sns_sano", "Tema SNS publicando sin fallos", t, build(t), set())

    t = resource(15, "alertas-email", "aws", "sns_topic", status=None)
    add(g, "aws_sns_fallando", "Tema SNS con 0,3 entregas fallidas por segundo", t,
        build(t, {"failed_per_second": 0.3}), {"failed_per_second_high"})

    vm = resource(16, "batch-detenida", "aws", "ec2_instance", status="stopped",
                  resource_id="i-0demo000016",
                  raw_data={"BlockDeviceMappings": [{"Ebs": {"VolumeId": "vol-0demo000017"}}],
                            "StateTransitionReason": "User initiated (2026-09-30 18:02:11 GMT)"})
    vol = resource(17, "batch-root", "aws", "ebs_volume", status="in-use",
                   resource_id="vol-0demo000017", raw_data={"Size": 100, "Attachments": [{"InstanceId": "i-0demo000016"}]})
    add(g, "aws_ec2_detenida_con_ebs", "EC2 detenida desde hace semanas con un EBS de 100 GB adjunto (sigue cobrando)", vm,
        ([], []), {"stopped_instance_storage"}, others=[vol],
        billing=billing(vol, 20, 0.27), expected_cost=("attributed", "high"))

    # ───────────────────────── GCP ─────────────────────────
    g = "GCP"
    vm = resource(20, "api-gce-sana", "gcp", "compute_instance")
    add(g, "gcp_vm_sana", "VM de Compute Engine normal, semana completa", vm, build(vm), {"capacity_visibility"})

    vm = resource(21, "api-gce-ociosa", "gcp", "compute_instance", resource_id="2000000000000000021")
    add(g, "gcp_vm_ociosa", "VM con CPU ~5 % toda la semana → candidata a máquina más pequeña", vm,
        build(vm, {"cpu_percent": 5}), {"cpu_percent_oversized", "capacity_visibility"},
        billing=billing(vm, 10, 1.2), expected_cost=("attributed", None))

    sql = {"status": "RUNNABLE", "raw_data": {"settings": {"dataDiskSizeGb": "100"}}}
    db = resource(22, "orders-db", "gcp", "cloudsql_instance", **sql)
    add(g, "gcp_sql_sana", "Cloud SQL Postgres sano (CPU 35 %, memoria 55 %, disco 45 %)", db, build(db), set())

    db = resource(23, "analytics-db", "gcp", "cloudsql_instance", **sql)
    add(g, "gcp_sql_ociosa", "Cloud SQL con CPU ~6 % toda la semana → reducir CPU", db,
        build(db, {"cpu_percent": 6}), {"cpu_percent_oversized"})

    db = resource(24, "cache-db", "gcp", "cloudsql_instance", **sql)
    add(g, "gcp_sql_cpu_baja_memoria_alta", "CPU ~6 % pero memoria ~88 % → alerta de memoria y NO reducir", db,
        build(db, {"cpu_percent": 6, "memory_percent": 88}), {"memory_percent_high"})

    db = resource(25, "reports-db", "gcp", "cloudsql_instance", **sql)
    add(g, "gcp_sql_disco_sobrado", "Cloud SQL de 100 GB usando ~5 GB toda la semana → reducir disco", db,
        build(db, {"filesystem_percent": 5}), {"filesystem_percent_oversized"},
        billing=[], expected_cost=("not_attributable", None))

    db = resource(26, "events-db", "gcp", "cloudsql_instance", **sql)
    growth = [45] * (WEEK - 24) + [60 + i for i in range(24)]  # crece 1 %/h las últimas 24 h
    add(g, "gcp_sql_disco_llenandose", "Disco subiendo 1 %/h hasta 84 % → alta ocupación y se llena en <24 h", db,
        build(db, {"filesystem_percent": [82, 82.5, 83, 83.5, 84, 84]}, {"filesystem_percent": growth}),
        {"filesystem_percent_high", "storage_exhaustion"})

    db = resource(27, "legacy-db", "gcp", "cloudsql_instance", status="RUNNABLE",
                  raw_data={"settings": {"dataDiskSizeGb": "50"}})
    add(g, "gcp_sql_disco_critico", "Disco al 95 % sostenido → alerta crítica", db,
        build(db, {"filesystem_percent": 95}), {"filesystem_percent_high"})

    topic = resource(28, "eventos", "gcp", "pubsub_topic", status=None)
    add(g, "gcp_pubsub_topic_sano", "Tema Pub/Sub con mensajes retenidos normales", topic, build(topic), set())

    sub = resource(29, "eventos-sub", "gcp", "pubsub_subscription", status=None)
    add(g, "gcp_pubsub_sana", "Suscripción Pub/Sub al día", sub, build(sub), set())

    sub = resource(30, "eventos-batch", "gcp", "pubsub_subscription", status=None)
    add(g, "gcp_pubsub_atascada", "Suscripción con mensajes sin confirmar hace ~20 min → crítica", sub,
        build(sub, {"oldest_message_seconds": 1200, "queued_messages": 15000}), {"oldest_message_seconds_high"})

    disk = resource(31, "old-data-disk", "gcp", "persistent_disk", status="READY", resource_id="3000000000000000031",
                    attributes={"users": [], "size_gb": 200, "disk_type": "pd-standard"})
    add(g, "gcp_disco_sin_adjuntar", "Persistent Disk de 200 GB sin ninguna VM que lo use", disk,
        ([], []), {"unattached_volume"}, billing=billing(disk, 14, 0.32), expected_cost=("attributed", "high"))

    ip = resource(32, "spare-ip", "gcp", "ip_address", status="RESERVED", resource_id="3000000000000000032",
                  attributes={"users": [], "address_type": "EXTERNAL", "address": "34.1.2.3"})
    add(g, "gcp_ip_sin_uso", "IP externa reservada hace 5 días que ningún recurso usa", ip, ([], []), {"unused_address"},
        billing=billing(ip, 5, 0.24), expected_cost=("attributed", "low"))

    ip = resource(33, "lb-ip", "gcp", "ip_address", status="IN_USE",
                  attributes={"users": ["web-forwarding-rule"], "address_type": "EXTERNAL", "address": "34.9.9.9"})
    add(g, "gcp_ip_en_uso", "IP externa usada por un balanceador → NO se recomienda liberar", ip, ([], []), set())

    # ─────────────── Datos faltantes / historial insuficiente ───────────────
    g = "Datos incompletos"
    vm = resource(40, "web-recien-creada", "aws", "ec2_instance")
    add(g, "historial_insuficiente", "EC2 con CPU ~6 % pero solo 2 días de historial → NO recomienda, pide más datos", vm,
        build(vm, {"cpu_percent": 6}, history_hours=48), {"insufficient_history", "capacity_visibility"})

    vm = resource(41, "web-sin-historial", "gcp", "compute_instance")
    add(g, "sin_historial", "VM recién conectada: métricas de ahora pero cero historial", vm,
        build(vm, {"cpu_percent": 6}, no_history=True), {"insufficient_history", "capacity_visibility"})

    vm = resource(42, "web-con-huecos", "aws", "ec2_instance")
    rec, hist = build(vm, {"cpu_percent": 6})
    for entry in hist:  # se pierde 1 de cada 3 horas (66 % de cobertura; el motor pide 80 %)
        entry["values"] = [v for i, v in enumerate(entry["values"]) if i % 3]
    add(g, "historial_con_huecos", "Semana completa pero con 1/3 de las horas perdidas → historial insuficiente", vm,
        (rec, hist), {"insufficient_history", "capacity_visibility"})

    vm = resource(43, "web-parcial", "aws", "ec2_instance")
    add(g, "ec2_sin_disco_local", "EC2 solo con EBS (como una t3.micro): sin métricas de disco local → NO es Missing metrics", vm,
        build(vm, skip_recent=("disk_read_bytes_per_second", "disk_write_bytes_per_second")),
        {"capacity_visibility"})

    vm = resource(48, "web-sin-cpu", "aws", "ec2_instance")
    add(g, "metricas_faltantes_parcial", "EC2 sin lecturas de CPU (métrica obligatoria) → Missing metrics", vm,
        build(vm, skip_recent=("cpu_percent",)), {"missing_telemetry", "capacity_visibility"})

    q = resource(44, "pagos-sin-datos", "aws", "sqs_queue", status=None)
    add(g, "metricas_faltantes_total", "Cola SQS sin ninguna lectura reciente (permisos o métricas caídas)", q,
        build(q, skip_recent=("queued_messages", "oldest_message_seconds")), {"missing_telemetry"})

    t = resource(45, "RedshiftSNS", "aws", "sns_topic", status=None)
    vm = resource(49, "web-misma-cuenta", "aws", "ec2_instance")
    vm_recent, _ = build(vm)
    add(g, "sns_sin_trafico", "Tema SNS que nadie usa, en una cuenta que sí reporta métricas → NO es Missing metrics", t,
        (vm_recent, []), set(), others=[vm])

    t = resource(50, "alertas-sin-datos", "aws", "sns_topic", status=None)
    add(g, "sns_cuenta_sin_datos", "Tema SNS sin datos y ninguna otra métrica de la cuenta (permisos o conexión) → Missing metrics", t,
        ([], []), {"missing_telemetry"})

    vm = resource(46, "web-prom-caido", "aws", "ec2_instance")
    add(g, "prometheus_caido", "Prometheus no responde: el motor no inventa salud ni alertas", vm,
        build(vm, {"cpu_percent": 95}), set(), telemetry_available=False, history_available=False)

    vm = resource(47, "web-apagada", "aws", "ec2_instance", status="stopped")
    add(g, "recurso_apagado", "EC2 detenida: se ignora aunque tenga métricas", vm,
        build(vm, {"cpu_percent": 6}), set())

    return s


def show(scenario):
    # `others`: other resources of the same account, evaluated together but not shown.
    findings, _ = evaluate([scenario["item"], *scenario.get("others", [])], scenario["recent"], scenario["history"],
                           now=NOW, telemetry_available=scenario.get("telemetry_available", True),
                           history_available=scenario.get("history_available", True))
    findings = [f for f in findings if f["resource_id"] == scenario["item"]["id"]]
    if "billing" in scenario:
        assess_costs(_BillingTable(scenario["billing"]), "demo", findings,
                     [scenario["item"], *scenario.get("others", [])], today=TODAY)
    got = {f["rule"] for f in findings}
    ok = got == scenario["expected"]
    costs = [f["cost"] for f in findings if f.get("cost")]
    if "expected_cost" in scenario:
        status, confidence = scenario["expected_cost"]
        cost = costs[0] if costs else {}
        ok = ok and cost.get("status") == status and ((cost.get("savings") or {}).get("confidence") == confidence)
    item = scenario["item"]
    own = [x for x in scenario["recent"] if x["metric"]["resource_id"] == item["resource_id"]]
    hours = max((len(h["values"]) for h in scenario["history"] if h["metric"]["resource_id"] == item["resource_id"]), default=0)
    print("─" * 82)
    print(f"{'✔' if ok else '✘'} [{scenario['name']}] {scenario['description']}")
    print(f"   Recurso: {item['name']} · {item['provider'].upper()} · {item['resource_type']} · "
          f"{len(own)}/{len(metrics_for(item))} métricas recientes · {hours} h de historial")
    if not findings:
        print("   → Sin hallazgos (el recurso se ve sano o no se evalúa).")
    for f in findings:
        ev = f.get("evidence", {})
        print(f"   → [{f['kind']} · {f['severity'].upper()} · {f['category']}] {f['title']}  ({f['rule']})")
        print(f"      {f['description']}")
        if ev.get("value") is not None:
            print(f"      Evidencia: valor={round(ev['value'], 2)} {ev.get('unit', '')} · umbral={ev.get('threshold')} · "
                  f"ventana={ev.get('window')} · lecturas={ev.get('samples')}")
        if ev.get("explanation"):
            print(f"      Por qué: {ev['explanation']}")
        for action in f.get("actions", [])[:2]:
            print(f"      · {action}")
        if f.get("cost"):
            show_cost(f["cost"])
    if not ok:
        print(f"   ✘ Esperado: {sorted(scenario['expected'])}  ·  Obtenido: {sorted(got)}")
    return ok


def show_cost(cost):
    if cost["status"] != "attributed":
        print(f"      $ Costo: no atribuible. {cost['reason']}")
        return
    currency = cost["currency"]
    print(f"      $ Costo observado: {cost['observed_amount']:,.2f} {currency} en {cost['days_covered']} día(s) facturados "
          f"({cost['period_start']} → {cost['period_end']}) · ~{cost['monthly_cost']:,.2f} {currency}/mes")
    savings = cost["savings"]
    if savings:
        print(f"      $ Ahorro estimado: {savings['monthly_amount']:,.2f} {currency}/mes · confianza {savings['confidence']} "
              f"· impacto sugerido {suggested_impact(cost)}")
        print(f"        Fórmula: {savings['formula']}")
        for assumption in savings["assumptions"]:
            print(f"        Supuesto: {assumption}")
    else:
        print(f"      $ Ahorro: no estimado. {cost['reason']}")


def main():
    all_scenarios = scenarios()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--escenario", choices=[x["name"] for x in all_scenarios], help="ejecutar solo uno")
    parser.add_argument("--grupo", choices=sorted({x["group"] for x in all_scenarios}), help="ejecutar solo un grupo")
    parser.add_argument("--lista", action="store_true", help="listar escenarios sin ejecutarlos")
    args = parser.parse_args()

    if args.lista:
        for x in all_scenarios:
            print(f"{x['group']:<18} {x['name']:<32} {x['description']}")
        return

    results, group = [], None
    for x in all_scenarios:
        if args.escenario not in (None, x["name"]) or args.grupo not in (None, x["group"]):
            continue
        if x["group"] != group:
            group = x["group"]
            print("\n" + "═" * 82 + f"\n  {group}\n" + "═" * 82)
        results.append(show(x))
    print("─" * 82)
    print(f"\n{sum(results)}/{len(results)} escenarios se comportaron como se esperaba.")


if __name__ == "__main__":
    main()
