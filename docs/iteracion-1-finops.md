# Iteración 1: recomendaciones FinOps con costo real

Base: la auditoría y propuesta técnica (bloques A–D aprobados). Alcance: AWS y GCP; Kubernetes queda fuera.

## Qué se implementó

| Bloque | Oportunidad de la matriz | Resultado |
|---|---|---|
| A. Atribución de costo real | 30, 31 (parcial) | Los hallazgos de costo muestran el **costo observado** en la facturación por recurso y, cuando la acción elimina el recurso, el **ahorro estimado** con fórmula, período, fuente, supuestos y confianza. |
| B. Persistent Disk sin adjuntar | 26 (y 11 unificada) | Inventario de discos de GCP; la regla `unattached_volume` ahora cubre EBS y Persistent Disk. |
| C. IP externa sin uso | 23 | Inventario de direcciones de GCP (regionales y globales); regla `unused_address`. |
| D. EC2 detenida con EBS | 2 | Regla `stopped_instance_storage`: volúmenes y GB que siguen cobrando. |
| Extra (detectado en pruebas) | — | Los volúmenes de una EC2 detenida ya no generan "Missing metrics", porque AWS no publica métricas de EBS mientras la instancia está detenida. |

### Separación de responsabilidades
- **Recolección:** `inventory/collectors/gcp.py` (`disks()`, `addresses()`).
- **Análisis técnico:** `recommendations/engine.py`. Las reglas de inventario están en `_inventory_reviews`; las verificaciones que permiten resolver hallazgos, en `_inventory_checks`.
- **Evaluación económica:** `recommendations/economics.py`, un módulo nuevo. Solo lee `cost_records` y nunca usa precios de lista.
- **Persistencia y flujo:** `recommendations/repository.py`. El impacto económico **sugerido** nunca pisa la decisión de una persona.

## Cómo se calcula el costo y el ahorro

1. **Cruce de facturación con inventario** (`economics.billing_key`):
   - GCP: `//compute.googleapis.com/projects/<n>/(zones|regions|global)/…/<tipo>/<id>` se convierte en `(resource_type, id)`. Tipos: `instances`, `disk`/`disks`, `addresses`/`globalAddresses`.
   - AWS: `i-…` se cruza con `ec2_instance`; `vol-…`, con `ebs_volume`.
   - El cruce solo ocurre dentro de la **misma organización y conexión**. Los formatos desconocidos (BigQuery, Cloud Storage, Logging) no se cruzan.
2. **Período:** los últimos `cost_lookback_days` (30). Solo cuentan los días con líneas de facturación.
3. **Fórmulas:**
   - `costo_mensual = (costo observado / días facturados) × 30`.
   - Ahorro (`unattached_volume`, `unused_address` y `stopped_instance_storage`, en este caso sumando todos los volúmenes adjuntos): `ahorro_mensual = costo_mensual`.
   - Rightsizing (`cpu_percent_oversized`, `filesystem_percent_oversized`): solo se muestra el **costo actual**. El ahorro no se estima porque falta el precio del tamaño alternativo.
4. **Confianza:** alta con ≥ 14 días facturados, media con ≥ 7 y baja con menos.
5. **Supuestos que se muestran:**
   - costo de uso **antes de créditos** (coherente con la página de costos);
   - los días sin facturación no cuentan en el promedio;
   - no se valoran snapshots ni reemplazos.
6. **No atribuible**, siempre con el motivo explícito:
   - no hay fuente de facturación configurada;
   - AWS sin datos por recurso (requiere activarlos y solo cubre EC2 por 14 días);
   - no hay líneas de facturación en el período (el billing llega con 24–48 h de retraso);
   - hay monedas distintas.
7. **Impacto sugerido:** ahorro ≥ `savings_high` (50) es **alto**, ≥ `savings_medium` (10) es **medio** y > 0 es **bajo**. Si una persona ya usó "Save priority", el impacto que ella eligió se conserva. El motivo de la prioridad incluye "Estimated savings: X/month (confianza)".

## Configuración

No hay variables de entorno nuevas. Los parámetros están en `backend/src/vanguard/recommendations/policy.py`:

| Parámetro | Valor | Uso |
|---|---|---|
| `cost_lookback_days` | 30 | Ventana de facturación |
| `cost_high_confidence_days` | 14 | Días para confianza alta |
| `cost_medium_confidence_days` | 7 | Días para confianza media |
| `savings_high` | 50 | Ahorro mensual para impacto alto (en la moneda de la cuenta) |
| `savings_medium` | 10 | Ahorro mensual para impacto medio |

**Permisos nuevos de GCP** (`infrastructure/policies/gcp-inventory-role.yaml`): `compute.disks.list`, `compute.addresses.list` y `compute.globalAddresses.list`. El último no estaba en la propuesta, pero lo necesitan las direcciones globales. Si faltan, el recolector reporta el error de ese tipo de recurso sin afectar al resto.

## Cambios por capa

| Capa | Archivos |
|---|---|
| Ingesta | `inventory/collectors/gcp.py`, `inventory/taxonomy.py`, `infrastructure/policies/gcp-inventory-role.yaml` |
| Análisis | `recommendations/analysis.py` (proyección de `BlockDeviceMappings`, `Size`, `StateTransitionReason` y `connection_id`; llamada a la evaluación económica) |
| Motor técnico | `recommendations/engine.py` |
| Evaluación económica | `recommendations/economics.py` (nuevo), `recommendations/policy.py`, `recommendations/priority.py` |
| Esquemas y persistencia | `recommendations/schemas.py` (`CostAssessment` y `Savings` opcionales en `Finding`), `recommendations/repository.py`. **No hay migración**: el costo viaja en el JSON `finding`. |
| Frontend | `RecommendationComponents.jsx` (bloque de costo, ahorro, días, confianza y "How this was calculated"; ahorro en la tarjeta compacta), `resourceTypes.js`, `resourceNames.js`, `global.css` |
| Demo | `scripts/demo_recomendaciones.py` (4 escenarios nuevos, facturación inventada y evaluación económica, 40/40), `scripts/demo_seed.py` (workspace de demo para la app), `docs/demo-recomendaciones.md` |
| Worker | `recommendations/worker.py`: omite workspaces cuyas conexiones están todas deshabilitadas |

## Pruebas

| Archivo | Qué cubre |
|---|---|
| `test_gcp_collector.py` | Disco y direcciones (regionales y globales): ID, región, zona, `users`, tipo y tamaño |
| `test_recommendations.py` | PD sin adjuntar (y sus casos negativos, más la resolución), IP sin uso (solo `EXTERNAL` + `RESERVED` + sin usuarios), EC2 detenida (suma de GB, sin duplicar con `unattached_volume`, sin "Missing metrics" en sus volúmenes, resolución) |
| `test_recommendation_economics.py` (nuevo) | Parseo de IDs; cruce solo con la misma conexión y organización; ventana; confianza; monedas mezcladas; rightsizing sin ahorro; alertas sin bloque de costo; EC2 detenida con varios volúmenes; costo cero; umbrales de impacto |
| `test_recommendation_workflow.py` | El costo se guarda y se expone por la API; el impacto sugerido es "alto"; la decisión humana se conserva al reanalizar; el análisis automático omite workspaces con todas sus conexiones deshabilitadas |

Resultado: **103 tests de backend OK** (1 se salta porque requiere `promtool`), `ruff` sin errores, 4 tests de frontend OK, `vite build` OK, demo 40/40.

**Verificación con datos reales (GCP):** el cruce de facturación con inventario pasó de **0 a 3 recursos**:

| Recurso | Costo mensual estimado |
|---|---|
| VM `demo-dev-postgresql` | 32.157 COP/mes |
| Disco `demo-dev-postgresql` | 7.740 COP/mes |
| IP del NAT | 9.598 COP/mes |

Los tres con 9 días facturados. El disco y la IP están en uso, así que no generan recomendación, que es lo correcto. La regla de EC2 detenida no se pudo comprobar en vivo porque la sesión de AWS Academy había expirado: está cubierta por tests y por el demo.

## Cómo verlo con datos de prueba

Las nubes de prueba rara vez tienen un disco suelto o una IP reservada con días de facturación acumulados. Por eso hay dos herramientas. Las dos usan el **código real** (motor, evaluación económica y persistencia); lo único inventado son los datos de entrada.

### En la terminal: `scripts/demo_recomendaciones.py`
Los escenarios con facturación inventada muestran costo observado, ahorro, confianza, impacto sugerido, fórmula y supuestos, o el motivo por el que no es atribuible. El script verifica también el estado del costo esperado (✔/✘). El reloj es fijo, así que las fechas aparecen en 2027.

```bash
PYTHONPATH=backend/src .venv/bin/python scripts/demo_recomendaciones.py --escenario gcp_disco_sin_adjuntar
```

### En la app: `scripts/demo_seed.py`
Crea un **workspace de demo aparte**: usuario `demo@vanguard.test` y tres conexiones deshabilitadas ("Demo GCP", "Demo AWS" y "Demo AWS Academy", esta última sin costo por recurso). Carga el inventario de todos los escenarios, inventa facturación por recurso y por servicio, y guarda un análisis completo. Tus datos reales no se tocan.

```bash
PYTHONPATH=backend/src .venv/bin/python scripts/demo_seed.py
```

```bash
PYTHONPATH=backend/src .venv/bin/python scripts/demo_seed.py --reset
```

- El usuario y la contraseña se generan en cada ejecución y quedan en `.vanguard/demo-credentials.txt`, que git ignora.
- En **Recommendations**, filtra *Focus = Cost* para ver las tarjetas con ahorro, costo actual o "no atribuible". En **Costs** aparece el gasto inventado.
- **No uses "Analyze now"** en el workspace de demo: el análisis manual consulta Prometheus, donde las métricas inventadas no existen, y reemplazaría la demo. Si pasa, vuelve a ejecutar el script.
- El análisis automático ya **no** reevalúa workspaces cuyas conexiones están todas deshabilitadas (`recommendations/worker.py`). Los workspaces sin ninguna conexión sí se siguen evaluando, para que se resuelvan los hallazgos de recursos eliminados.

## Limitaciones conocidas

- **Moneda de los umbrales:** 50 y 10 se aplican en la moneda de la cuenta. Con **COP**, casi cualquier ahorro queda como "alto". Hay que ajustarlos en `policy.py` según la moneda, o definir umbrales por moneda en una iteración posterior.
- **AWS:** sin datos por recurso en Cost Explorer (cuentas Academy, o la opción no activada), las recomendaciones de AWS muestran el costo como **no atribuible**. No se usan precios de lista.
- **Retraso del billing de GCP:** un recurso nuevo no tiene costo atribuible durante 1–2 días.
- **El costo cambia cada día**, así que la `version` del hallazgo sube en cada análisis con datos nuevos. Si alguien tiene abierta la tarjeta, puede recibir "This recommendation has changed. Refresh…".
- **Ahorro bruto, no neto:** con créditos, el ahorro real en factura puede ser menor.

## Estado de las oportunidades tras la iteración

| Estado | Oportunidades |
|---|---|
| **Funcionando de punta a punta en GCP** (técnica + costo + ahorro) | 26 Persistent Disk sin adjuntar, 23 IP externa sin uso |
| **Funcionando: técnica + costo actual (sin ahorro)** | 22 rightsizing de VM en GCP, 25 Cloud SQL (si su `global_name` se puede cruzar; hoy no aparece en el billing de prueba) |
| **Funcionando: técnica; costo no atribuible en AWS sin datos por recurso** | 11 EBS sin adjuntar, 2 EC2 detenida con EBS, 1 rightsizing EC2 |
| **Base transversal disponible** | 30 atribución de costo; 31 estimación con fórmula y confianza (solo para eliminar recursos) |
| **Requieren más datos** | 1, 22 y 25 con ahorro (precio del tamaño alternativo); 12 (espacio usado de EBS: agente); 10 DynamoDB inactiva (historial de consumo); 16 Elastic IP (`ec2:DescribeAddresses`) |
| **Pendientes** | 3–9, 13–15, 17–21, 24, 27–29, y la verificación de resultado de 32 |
