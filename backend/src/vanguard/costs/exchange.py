"""Official USD/COP TRM cache. Original billing amounts are never modified."""

import json
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

SOURCE = "Superintendencia Financiera de Colombia"
SOURCE_URL = "https://www.datos.gov.co/Econom-a-y-Finanzas/Tasa-de-Cambio-Representativa-del-Mercado-TRM/32sa-8pi3"
API_URL = "https://www.datos.gov.co/resource/32sa-8pi3.json"
MAX_CARRY_DAYS = 7

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS cost_exchange_rates (
    valid_from DATE PRIMARY KEY,
    valid_to DATE NOT NULL CHECK (valid_to >= valid_from),
    cop_per_usd NUMERIC(24, 10) NOT NULL CHECK (cop_per_usd > 0),
    source TEXT NOT NULL,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS cost_exchange_sync (
    singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
    last_attempt_at TIMESTAMPTZ,
    last_success_at TIMESTAMPTZ,
    last_error TEXT
);
INSERT INTO cost_exchange_sync (singleton) VALUES (TRUE) ON CONFLICT DO NOTHING;
"""

# Rates are selected for each usage date, never from a future trading day. A short
# carry-forward keeps cached data usable during an outage and is disclosed below.
NORMALIZED_CTE = f"""
WITH normalized_costs AS (
    SELECT r.owner_id, r.connection_id, r.provider, r.scope_id, r.level, r.usage_date,
           r.service, r.category, r.resource_id, r.resource_name, r.region, r.estimated,
           'USD'::text AS currency,
           CASE WHEN r.currency = 'USD' THEN r.amount
                WHEN r.currency = 'COP' THEN r.amount / fx.cop_per_usd END AS amount,
           r.currency AS original_currency, r.amount AS original_amount,
           fx.valid_from AS exchange_date, fx.valid_to AS exchange_valid_to
    FROM cost_records r
    LEFT JOIN LATERAL (
        SELECT e.cop_per_usd, e.valid_from, e.valid_to
        FROM cost_exchange_rates e
        WHERE e.valid_from <= r.usage_date AND e.valid_to >= r.usage_date - {MAX_CARRY_DAYS}
        ORDER BY e.valid_from DESC LIMIT 1
    ) fx ON r.currency = 'COP'
    WHERE r.currency = 'USD' OR (r.currency = 'COP' AND fx.cop_per_usd IS NOT NULL)
)
"""


def today():
    return datetime.now(ZoneInfo("America/Bogota")).date()


def fetch_rates(*, on_date=None, days=190):
    on_date = on_date or today()
    start = on_date - timedelta(days=days)
    query = urlencode({
        "$where": f"vigenciadesde < '{(on_date + timedelta(days=1)).isoformat()}T00:00:00' AND vigenciahasta >= '{start.isoformat()}T00:00:00'",
        "$order": "vigenciadesde DESC", "$limit": 1000,
    })
    request = Request(API_URL + "?" + query, headers={"Accept": "application/json", "User-Agent": "Vanguard/1.0 (official TRM cache)"})
    with urlopen(request, timeout=20) as response:
        body = json.load(response)
    if not isinstance(body, list) or not body:
        raise ValueError("Official TRM returned no rates")
    rates = []
    for row in body:
        try:
            rate = Decimal(str(row["valor"]))
            start_date = date.fromisoformat(row["vigenciadesde"][:10])
            end_date = date.fromisoformat(row["vigenciahasta"][:10])
            if not rate.is_finite() or rate <= 0 or row["unidad"] != "COP" or end_date < start_date:
                raise ValueError("Invalid official TRM record")
            if start_date <= on_date:
                rates.append((start_date, end_date, rate, SOURCE))
        except (KeyError, TypeError, InvalidOperation) as exc:
            raise ValueError("Invalid official TRM record") from exc
    if not rates:
        raise ValueError("Official TRM has no current or historical rates")
    return rates


def claim_refresh(connection, *, force=False):
    row = connection.execute("""
        UPDATE cost_exchange_sync SET last_attempt_at = NOW()
        WHERE singleton
          AND (%s OR last_attempt_at IS NULL OR last_attempt_at < NOW() - INTERVAL '1 hour')
          AND (%s OR last_success_at IS NULL OR (last_success_at AT TIME ZONE 'America/Bogota')::date < (NOW() AT TIME ZONE 'America/Bogota')::date)
        RETURNING singleton
    """, (force, force)).fetchone()
    connection.commit()
    return row is not None


def save_rates(connection, rates):
    if not rates:
        raise ValueError("Cannot replace TRM cache with an empty result")
    with connection.cursor() as cursor:
        cursor.executemany("""
            INSERT INTO cost_exchange_rates (valid_from, valid_to, cop_per_usd, source)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (valid_from) DO UPDATE SET valid_to = EXCLUDED.valid_to,
                cop_per_usd = EXCLUDED.cop_per_usd, source = EXCLUDED.source, fetched_at = NOW()
        """, rates)
    connection.execute("UPDATE cost_exchange_sync SET last_success_at = NOW(), last_error = NULL WHERE singleton")
    connection.commit()


def metadata(connection, owner_id, start, end, provider, infrastructure):
    provider_clause = "AND provider = %s" if provider != "all" else ""
    category_column = "category" if provider == "all" else "service"
    infrastructure_clause = f"AND {category_column} = %s" if infrastructure else ""
    values = [owner_id, start, end]
    if provider != "all":
        values.append(provider)
    if infrastructure:
        values.append(infrastructure)
    native = connection.execute(f"""
        SELECT currency, SUM(amount) AS amount FROM cost_records
        WHERE owner_id = %s AND level = 'service' AND usage_date >= %s AND usage_date < %s
              {provider_clause} {infrastructure_clause}
        GROUP BY currency ORDER BY currency
    """, values).fetchall()
    missing = connection.execute(f"""
        SELECT r.currency, SUM(r.amount) AS amount, COUNT(*) AS records
        FROM cost_records r
        WHERE owner_id = %s AND level = 'service' AND usage_date >= %s AND usage_date < %s
              {provider_clause} {infrastructure_clause}
          AND currency <> 'USD' AND (currency <> 'COP' OR NOT EXISTS (
              SELECT 1 FROM cost_exchange_rates e WHERE e.valid_from <= r.usage_date
                AND e.valid_to >= r.usage_date - {MAX_CARRY_DAYS}))
        GROUP BY r.currency ORDER BY r.currency
    """, values).fetchall()
    # Include the comparison period: an incomplete previous total is not a valid percentage.
    comparison_values = [owner_id, start-(end-start), start, *values[3:]]
    missing_previous = connection.execute(f"""
        SELECT COUNT(*) AS count FROM cost_records r
        WHERE owner_id = %s AND level = 'service' AND usage_date >= %s AND usage_date < %s
              {provider_clause} {infrastructure_clause}
          AND currency <> 'USD' AND (currency <> 'COP' OR NOT EXISTS (
              SELECT 1 FROM cost_exchange_rates e WHERE e.valid_from <= r.usage_date
                AND e.valid_to >= r.usage_date - {MAX_CARRY_DAYS}))
    """, comparison_values).fetchone()["count"]
    stale = connection.execute(NORMALIZED_CTE + f"""
        SELECT COUNT(*) AS count FROM normalized_costs
        WHERE owner_id = %s AND level = 'service' AND usage_date >= %s AND usage_date < %s
              {provider_clause} {infrastructure_clause}
          AND original_currency = 'COP' AND exchange_valid_to < usage_date
    """, values).fetchone()["count"]
    current = connection.execute("""
        SELECT valid_from, valid_to, cop_per_usd, fetched_at FROM cost_exchange_rates
        WHERE valid_from <= %s ORDER BY valid_from DESC LIMIT 1
    """, (today(),)).fetchone()
    sync = connection.execute("SELECT last_success_at, last_error FROM cost_exchange_sync WHERE singleton").fetchone()
    needs_conversion = any(row["currency"] != "USD" for row in native)
    status = "partial" if missing else "stale" if stale or (needs_conversion and current and current["valid_to"] < today()) else "ready" if needs_conversion else "not_needed"
    return {
        "status": status, "method": "daily_historical_trm", "source": SOURCE, "source_url": SOURCE_URL,
        "cop_per_usd": float(current["cop_per_usd"]) if current else None,
        "valid_from": current["valid_from"] if current else None,
        "valid_to": current["valid_to"] if current else None,
        "updated_at": sync["last_success_at"] if sync else None,
        "last_error": sync["last_error"] if sync else None,
        "original_totals": [{**row, "amount": float(row["amount"])} for row in native],
        "unconverted": [{**row, "amount": float(row["amount"])} for row in missing],
        "comparison_complete": missing_previous == 0,
    }
