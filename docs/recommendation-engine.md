# Motor de recomendaciones

El motor separa alertas operativas, revisiones de capacidad/coste y sugerencias de observabilidad. El dashboard muestra estadísticas y prioridades; `#recommendations` permite filtrar por tipo, severidad, categoría y proveedor, consultar evidencia y abrir el historial del recurso.

## Investigación y criterios

Se tomaron como referencias [AWS Compute Optimizer](https://docs.aws.amazon.com/compute-optimizer/latest/ug/rightsizing-preferences.html), [Azure Advisor](https://learn.microsoft.com/en-us/azure/advisor/advisor-how-to-performance-resize-high-usage-vm-recommendations), [Google Compute Engine](https://docs.cloud.google.com/compute/docs/instances/apply-machine-type-recommendations-for-instances), las [plantillas de Zabbix](https://www.zabbix.com/integrations/linux) y las señales de [Google SRE](https://sre.google/sre-book/monitoring-distributed-systems/). Estos productos combinan historial, persistencia, percentiles, límites y margen; los porcentajes no son un estándar universal.

La política de Vanguard se centraliza en `backend/src/vanguard/recommendations/policy.py`:

| Regla | Condición inicial |
| --- | --- |
| CPU, memoria y disco bajo presión | ≥80% advertencia / ≥90% crítico, con al menos cuatro muestras que cubran 15 minutos, sin huecos mayores de 10 minutos |
| CPU posiblemente sobredimensionada | P95 de máximos horarios <20%, máximo semanal y reciente <50%, sin presión reciente conocida en memoria/disco |
| Almacenamiento posiblemente sobredimensionado | Todos los máximos horarios y valores recientes <20%; muestra capacidad y uso estimado cuando existe información en GB |
| Agotamiento de almacenamiento | Uso actual ≥70%, tendencia creciente convincente del último día y proyección a lleno ≤24 horas |
| Tráfico fuera del rango habitual | Mínimo sostenido ≥3× P95 de máximos horarios de la semana, excluyendo las últimas tres horas; suelo de 1 MiB/s |
| Mensajes esperando | Edad sostenida ≥300/900 segundos; adaptar al SLO de la cola |
| Entregas SNS fallidas | Tasa positiva sostenida |
| EBS sin adjuntar | Lista de adjuntos vacía, estado `available` e inventario reciente |

Las revisiones semanales requieren ≥80% de los 168 intervalos horarios, una separación de al menos seis días entre primera y última observación y ≥6 muestras de origen por intervalo. Se preservan máximos horarios para conservar picos; el percentil mostrado es P95 de esos máximos. Se rechazan datos inválidos, futuros o caducados y nunca se interpreta ausencia como uso cero.

La ocupación de disco hoy se evalúa para Cloud SQL. EC2/GCE no proporcionan RAM ni ocupación del filesystem mediante las métricas nativas actuales; el tráfico y el I/O tampoco equivalen a porcentajes de capacidad. Las oportunidades de reducción indican revisar tamaños y restricciones, sin asignar un ahorro monetario o SKU destino no comprobados. [Cloud SQL storage shrink](https://docs.cloud.google.com/sql/docs/postgres/about-storage-shrink) tiene requisitos de elegibilidad, tamaño mínimo, indisponibilidad y rendimiento.

`engine.py` contiene reglas puras, `telemetry.py` consulta un historial acotado y `api.py` aplica filtros y contratos. Las consultas se restringen a la organización activa; `owner_id` es la etiqueta histórica de su identidad. Los hallazgos se recalculan al consultar; sus IDs son estables y desaparecen cuando dejan de cumplirse las condiciones. Una caída de telemetría se muestra como información no evaluada. Si sólo falla el historial, las alertas recientes continúan.

Consultar [la revisión de seguridad y organizaciones](security-and-organizations.md) para migración, permisos, configuración y comprobaciones.
