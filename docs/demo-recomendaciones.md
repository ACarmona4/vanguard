# Demo del motor de recomendaciones

Resultados de `scripts/demo_recomendaciones.py`: el motor real de Vanguard (`vanguard.recommendations.engine.evaluate`) ejecutado sobre recursos de AWS y GCP con métricas inventadas, sin nube ni Prometheus.

- Cada recurso recibe **todas** las métricas del catálogo de Vanguard: lecturas cada 5 min de los últimos 30 min y **7 días de historial** por hora, con ±5 % de ruido. Los escenarios de *Datos incompletos* quitan datos a propósito.
- Cada escenario declara qué reglas espera; ✔ significa que el motor devolvió exactamente esas.

**Resultado: 40/40 escenarios se comportaron como se esperaba.**

## Umbrales del motor

Definidos en `backend/src/vanguard/recommendations/policy.py`:

| Regla | Umbral |
|---|---|
| Alerta de presión (CPU, memoria, disco) | Warning ≥ 80 %, critical ≥ 90 %, sostenido 15 min (se usa la lectura **mínima**) |
| Huecos permitidos en la ventana de 15 min | máx. 10 min entre lecturas |
| Historial para ahorro | ≥ 135 lecturas por hora (80 % de 7 días) |
| CPU sobredimensionada | p95 < 20 % **y** pico semanal < 50 %, sin presión en otro recurso |
| Disco sobredimensionado (Cloud SQL) | pico semanal < 20 % |
| Disco llenándose | uso actual 70–100 %, tendencia de 24 h con R² ≥ 0,7 y lleno en ≤ 24 h |
| Cola (SQS / Pub/Sub) | mensaje más viejo: warning ≥ 300 s, critical ≥ 900 s |
| Entregas fallidas (SNS) | cualquier valor > 0 sostenido |
| Crecimiento de red | > máx(1 MiB/s, 3 × p95 semanal) sostenido 15 min |
| Disco sin adjuntar (EBS / Persistent Disk) | `available` sin `Attachments` (AWS) o `READY` sin `users` (GCP), con inventario reciente |
| IP externa sin uso (GCP) | `EXTERNAL`, `RESERVED` y sin `users` |
| EC2 detenida con almacenamiento | `stopped` con volúmenes EBS adjuntos en el inventario |
| Métricas faltantes | alguna métrica **obligatoria** del catálogo sin lecturas en los últimos 20 min (el disco local de EC2 es opcional; SNS, SQS y DynamoDB solo se exigen si toda la cuenta está en silencio) |

## Cómo ejecutarlo

Desde la raíz del repo:

```bash
PYTHONPATH=backend/src .venv/bin/python scripts/demo_recomendaciones.py
```

Opciones: `--grupo AWS|GCP|"Datos incompletos"`, `--escenario <nombre>`, `--lista`.

## AWS

### ✔ [aws_ec2_sana] EC2 normal: CPU ~35 %, red y disco moderados, semana completa

```text
   Recurso: web-sana · AWS · ec2_instance · 5/5 métricas recientes · 168 h de historial
   → [suggestion · INFO · observability] Add memory and disk metrics  (capacity_visibility)
      Memory use and disk space are unavailable for this VM.
      Por qué: Review memory, disk and network use before choosing a smaller VM.
      · Add an agent to collect memory and disk usage.
      · Check the VM's network limit before resizing.
```

**Qué sucedió:**

La EC2 tiene sus 5 métricas al día y una semana completa de historial, con CPU alrededor de 35 %. No supera el 80 % de CPU (umbral de alerta) ni está por debajo del 20 % (umbral para recomendar reducir), así que el motor no genera ninguna alerta ni recomendación de costo.

La única salida es la sugerencia **"Add memory and disk metrics"**, que el motor añade a **toda** VM con métricas recientes. AWS no reporta memoria ni espacio en disco sin un agente, y el catálogo de Vanguard todavía no lee esas métricas, así que esta sugerencia aparece siempre en las VMs.

### ✔ [aws_ec2_ociosa] EC2 con CPU ~6 % toda la semana → candidata a instancia más pequeña

```text
   Recurso: web-ociosa · AWS · ec2_instance · 5/5 métricas recientes · 168 h de historial
   → [recommendation · INFO · cost] Low CPU usage  (cpu_percent_oversized)
      CPU usage was usually 6.3% or lower over the last 7 days.
      Evidencia: valor=6.26 % · umbral=20 · ventana=7d · lecturas=168
      Por qué: 95% of hourly CPU peaks were at or below 6.3%. Highest peak: 6.3%. Based on 168 hourly readings over seven days.
      · Review a smaller instance or CPU tier.
      · Check memory, network, I/O, CPU credits, availability and licensing before resizing.
   → [suggestion · INFO · observability] Add memory and disk metrics  (capacity_visibility)
      Memory use and disk space are unavailable for this VM.
      Por qué: Review memory, disk and network use before choosing a smaller VM.
      · Add an agent to collect memory and disk usage.
      · Check the VM's network limit before resizing.
```

**Qué sucedió:**

La CPU estuvo cerca de 6 % durante las 168 horas de la semana. El motor revisa tres condiciones antes de sugerir una instancia más pequeña:

1. **Historial suficiente:** al menos 135 lecturas por hora (80 % de 168) y que cubran ~7 días. ✔ 168.
2. **Percentil 95 por debajo de 20 %:** el 95 % de las horas estuvieron en ≤ 6,3 %. ✔
3. **Ningún pico ≥ 50 %** en la semana ni en los últimos minutos, y ningún otro recurso (memoria, disco) presionado. ✔

Al cumplirse las tres, genera **"Low CPU usage"** (categoría *cost*). Las acciones piden revisar memoria, red, créditos de CPU y licencias antes de cambiar el tamaño, porque el motor solo ve la CPU.

### ✔ [aws_ec2_ociosa_con_pico] CPU ~6 % pero un pico semanal de 92 % → NO debe recomendar reducir

```text
   Recurso: batch-con-pico · AWS · ec2_instance · 5/5 métricas recientes · 168 h de historial
   → [suggestion · INFO · observability] Add memory and disk metrics  (capacity_visibility)
      Memory use and disk space are unavailable for this VM.
      Por qué: Review memory, disk and network use before choosing a smaller VM.
      · Add an agent to collect memory and disk usage.
      · Check the VM's network limit before resizing.
```

**Qué sucedió:**

Es el mismo caso que el anterior (CPU ~6 % casi toda la semana), pero una de las 168 horas tiene un pico de **92 %**, como un job semanal pesado.

El percentil 95 sigue siendo bajo (~6 %), pero el motor también exige que el **pico máximo de la semana** esté por debajo de 50 %. Como 92 % lo supera, **no recomienda reducir**: si la instancia fuera más pequeña, ese job no tendría capacidad.

Es intencional: es preferible perder un ahorro que recomendar un cambio que rompa una carga real. Lo cubre el test `test_historical_cpu_bursts_block_reduction_even_with_low_p95`.

### ✔ [aws_ec2_cpu_alta] EC2 con CPU ~84 % sostenida 30 min → alerta warning

```text
   Recurso: checkout-warning · AWS · ec2_instance · 5/5 métricas recientes · 168 h de historial
   → [alert · WARNING · performance] High CPU usage  (cpu_percent_high)
      CPU usage stayed at 84.0% or higher for 15 minutes.
      Evidencia: valor=84.0 % · umbral=80 · ventana=15m · lecturas=4
      Por qué: The value shown is the lowest reading in this period.
      · Review the processes or queries using the most CPU.
      · Check peak demand before changing capacity.
   → [suggestion · INFO · observability] Add memory and disk metrics  (capacity_visibility)
      Memory use and disk space are unavailable for this VM.
      Por qué: Review memory, disk and network use before choosing a smaller VM.
      · Add an agent to collect memory and disk usage.
      · Check the VM's network limit before resizing.
```

**Qué sucedió:**

Las últimas 6 lecturas de CPU (30 min, una cada 5 min) están entre 83 % y 86 %. Para las alertas de presión, el motor toma **solo los últimos 15 minutos** (4 lecturas), exige que no haya huecos de más de 10 minutos entre ellas, y usa **la lectura más baja** de ese tramo: 84 %.

Como 84 % ≥ 80 % (warning) pero < 90 % (critical), la alerta es **WARNING**. Usar la mínima significa que la alerta solo salta si **todas** las lecturas superan el umbral, lo que evita alertas por picos momentáneos.

### ✔ [aws_ec2_cpu_critica] EC2 con CPU >90 % sostenida → alerta crítica

```text
   Recurso: checkout-critico · AWS · ec2_instance · 5/5 métricas recientes · 168 h de historial
   → [alert · CRITICAL · performance] Critical CPU usage  (cpu_percent_high)
      CPU usage stayed at 94.0% or higher for 15 minutes.
      Evidencia: valor=94.0 % · umbral=90 · ventana=15m · lecturas=4
      Por qué: The value shown is the lowest reading in this period.
      · Review the processes or queries using the most CPU.
      · Check peak demand before changing capacity.
   → [suggestion · INFO · observability] Add memory and disk metrics  (capacity_visibility)
      Memory use and disk space are unavailable for this VM.
      Por qué: Review memory, disk and network use before choosing a smaller VM.
      · Add an agent to collect memory and disk usage.
      · Check the VM's network limit before resizing.
```

**Qué sucedió:**

Es la misma lógica que el caso anterior, pero las 4 lecturas de los últimos 15 minutos están todas sobre 90 % (la mínima es 94 %), así que la alerta sube a **CRITICAL**.

En la app, esta alerta es la que más prioridad recibe. Si la CPU vuelve a bajar, en el siguiente análisis la alerta pasa a "No longer detected".

### ✔ [aws_ec2_pico_suelto] EC2 con un único pico de 97 % → NO debe alertar

```text
   Recurso: checkout-pico · AWS · ec2_instance · 5/5 métricas recientes · 168 h de historial
   → [suggestion · INFO · observability] Add memory and disk metrics  (capacity_visibility)
      Memory use and disk space are unavailable for this VM.
      Por qué: Review memory, disk and network use before choosing a smaller VM.
      · Add an agent to collect memory and disk usage.
      · Check the VM's network limit before resizing.
```

**Qué sucedió:**

Una sola lectura de 97 % entre lecturas de ~30 %. De las 4 lecturas de los últimos 15 minutos, la más baja es ~31 %, que está muy por debajo de 80 %, así que **no hay alerta**.

Un pico de 5 minutos no es una saturación sostenida y no justifica que alguien actúe. Lo cubre el test `test_single_spikes_and_short_windows_do_not_alert`.

### ✔ [aws_ec2_trafico_red] EC2 enviando 6 MiB/s cuando lo normal es 0,3 MiB/s → crecimiento de red

```text
   Recurso: api-trafico · AWS · ec2_instance · 5/5 métricas recientes · 168 h de historial
   → [recommendation · WARNING · performance] Higher network traffic  (network_out_bytes_per_second_growth)
      Traffic stayed above its usual weekly range for 15 minutes.
      Evidencia: valor=6175736.42 B/s · umbral=1048576 · ventana=15m · lecturas=4
      Por qué: Traffic stayed at or above 5.9 MiB/s. The weekly reference rate is 0.3 MiB/s.
      · Check the source of the extra traffic and its transfer costs.
      · Check the link's bandwidth limit before adding capacity.
   → [suggestion · INFO · observability] Add memory and disk metrics  (capacity_visibility)
      Memory use and disk space are unavailable for this VM.
      Por qué: Review memory, disk and network use before choosing a smaller VM.
      · Add an agent to collect memory and disk usage.
      · Check the VM's network limit before resizing.
```

**Qué sucedió:**

La red de salida lleva 15 minutos en ~6 MiB/s, cuando durante la semana estuvo en ~0,3 MiB/s. El motor calcula:

- **Referencia semanal:** percentil 95 del historial, excluyendo las últimas 3 horas para no contaminarla con el cambio actual: ~0,3 MiB/s.
- **Umbral:** el mayor entre **1 MiB/s** y **3 × la referencia**. Aquí 3 × 0,3 = 0,9, así que el umbral es 1 MiB/s.
- **Mínima de los últimos 15 min:** ~5,9 MiB/s, que supera el umbral.

El resultado es **"Higher network traffic"**. Es una *recommendation*, no una alerta de saturación: el motor no conoce el límite de ancho de banda de la instancia, así que solo avisa del cambio y sugiere revisar el origen del tráfico y los costos de transferencia.

### ✔ [aws_ebs_sano] Volumen EBS adjunto y con I/O normal

```text
   Recurso: vol-datos · AWS · ebs_volume · 3/3 métricas recientes · 168 h de historial
   → Sin hallazgos (el recurso se ve sano o no se evalúa).
```

**Qué sucedió:**

El volumen está adjunto a una instancia y tiene sus 3 métricas (lectura, escritura, cola de operaciones) al día. Para EBS el motor solo evalúa si está **suelto**; no tiene reglas de rendimiento. Como está adjunto y con datos, no hay hallazgos.

### ✔ [aws_ebs_suelto] Volumen EBS sin adjuntar (sigue cobrando)

```text
   Recurso: vol-huerfano · AWS · ebs_volume · 3/3 métricas recientes · 168 h de historial
   → [recommendation · INFO · cost] Unattached volume  (unattached_volume)
      This volume is not attached to an instance and may still incur storage charges.
      Evidencia: valor=0 attachments · umbral=None · ventana=inventory · lecturas=0
      Por qué: No attached instances in the latest inventory.
      · Check whether the volume is still needed.
      · Confirm ownership and backups before taking a snapshot or deleting it.
```

**Qué sucedió:**

El inventario reporta el volumen con estado `available` y una lista `Attachments` vacía, es decir, sin ninguna instancia conectada. El motor exige tres condiciones:

1. Estado `available`.
2. `Attachments` presente **y vacío**. Si el campo falta, no asume que está suelto.
3. Inventario **reciente** (visto en los últimos 30 min), para no recomendar sobre datos viejos.

Genera **"Unattached volume"** (*cost*): un EBS sin usar sigue cobrando por GB. Las acciones piden confirmar dueño y respaldos antes de borrarlo, porque podría guardar datos importantes. Esta regla no depende de métricas: funciona aunque Prometheus no responda.

### ✔ [aws_dynamodb_sana] Tabla DynamoDB con lecturas/escrituras normales

```text
   Recurso: sessions · AWS · dynamodb_table · 2/2 métricas recientes · 168 h de historial
   → Sin hallazgos (el recurso se ve sano o no se evalúa).
```

**Qué sucedió:**

La tabla tiene sus 2 métricas (unidades de lectura y escritura consumidas). Hoy el motor **no tiene reglas** específicas para DynamoDB: solo verifica que lleguen las métricas. Como llegan, no hay hallazgos.

### ✔ [aws_sqs_sana] Cola SQS procesando al día (mensaje más viejo ~15 s)

```text
   Recurso: pedidos-queue · AWS · sqs_queue · 2/2 métricas recientes · 168 h de historial
   → Sin hallazgos (el recurso se ve sano o no se evalúa).
```

**Qué sucedió:**

La cola tiene mensajes pendientes, pero el más viejo lleva ~15 s esperando. El umbral de warning es **300 s (5 min)**, así que no hay alerta. El motor no alerta por *cantidad* de mensajes, solo por **cuánto esperan**: una cola con muchos mensajes que se procesan rápido está sana.

### ✔ [aws_sqs_lenta] SQS con mensajes esperando ~6 min → warning

```text
   Recurso: pagos-lenta · AWS · sqs_queue · 2/2 métricas recientes · 168 h de historial
   → [alert · WARNING · reliability] Messages are waiting too long  (oldest_message_seconds_high)
      Message wait time stayed at 361.322 seconds or more for 15 minutes.
      Evidencia: valor=361.32 s · umbral=300 · ventana=15m · lecturas=4
      Por qué: The value shown is the lowest reading in the last 15 minutes.
      · Check consumers, retries and the dead-letter queue.
      · Review the queue's delivery target and capacity.
```

**Qué sucedió:**

El mensaje más viejo lleva ~6 min esperando de forma sostenida. La mínima de los últimos 15 minutos es ~361 s, que supera los 300 s (warning) pero no llega a los 900 s (critical), así que la alerta es **WARNING**.

El valor (361 s) es menor que el que puse en el escenario (380 s) por el ±5 % de ruido y porque el motor muestra la **mínima** de las 4 lecturas. La causa típica es un consumidor lento, caído o fallando; por eso las acciones apuntan a revisar consumidores, reintentos y la *dead-letter queue*.

### ✔ [aws_sqs_atascada] SQS con mensajes esperando ~16 min → crítica

```text
   Recurso: pagos-atascada · AWS · sqs_queue · 2/2 métricas recientes · 168 h de historial
   → [alert · CRITICAL · reliability] Messages are waiting too long  (oldest_message_seconds_high)
      Message wait time stayed at 931.831 seconds or more for 15 minutes.
      Evidencia: valor=931.83 s · umbral=900 · ventana=15m · lecturas=4
      Por qué: The value shown is the lowest reading in the last 15 minutes.
      · Check consumers, retries and the dead-letter queue.
      · Review the queue's delivery target and capacity.
```

**Qué sucedió:**

La misma lógica que la anterior, pero la mínima de los últimos 15 minutos es ~932 s (más de 15 min), por encima de **900 s**, así que la alerta es **CRITICAL**: probablemente no hay ningún consumidor procesando la cola.

### ✔ [aws_sns_sano] Tema SNS publicando sin fallos

```text
   Recurso: notificaciones · AWS · sns_topic · 2/2 métricas recientes · 168 h de historial
   → Sin hallazgos (el recurso se ve sano o no se evalúa).
```

**Qué sucedió:**

El tema publica ~3 mensajes por segundo y tiene 0 entregas fallidas. La regla de SNS solo salta si las **entregas fallidas** son mayores que 0 de forma sostenida, así que no hay hallazgos.

### ✔ [aws_sns_fallando] Tema SNS con 0,3 entregas fallidas por segundo

```text
   Recurso: alertas-email · AWS · sns_topic · 2/2 métricas recientes · 168 h de historial
   → [alert · WARNING · reliability] Message deliveries are failing  (failed_per_second_high)
      At least 0.285 deliveries per second failed for 15 minutes.
      Evidencia: valor=0.28 messages/s · umbral=0 · ventana=15m · lecturas=4
      Por qué: The value shown is the lowest reading in the last 15 minutes.
      · Check consumers, retries and the dead-letter queue.
      · Review the queue's delivery target and capacity.
```

**Qué sucedió:**

Las entregas fallidas se mantuvieron en ~0,3 por segundo durante 15 minutos (mínima 0,285). En SNS el umbral es **cualquier valor sostenido mayor que 0**, porque cada fallo es un mensaje que no llegó a su destino: un email, un webhook o una cola.

La alerta es **WARNING** (*reliability*). Esta regla no tiene nivel crítico.

## GCP

### ✔ [gcp_vm_sana] VM de Compute Engine normal, semana completa

```text
   Recurso: api-gce-sana · GCP · compute_instance · 5/5 métricas recientes · 168 h de historial
   → [suggestion · INFO · observability] Add memory and disk metrics  (capacity_visibility)
      Memory use and disk space are unavailable for this VM.
      Por qué: Review memory, disk and network use before choosing a smaller VM.
      · Add an agent to collect memory and disk usage.
      · Check the VM's network limit before resizing.
```

**Qué sucedió:**

Es lo mismo que `aws_ec2_sana`, pero en Compute Engine. Las 5 métricas de GCP (CPU, red de entrada y salida, lectura y escritura de disco) están al día con valores normales. Solo aparece la sugerencia de agente de memoria y disco, por la misma razón que en AWS: el Ops Agent de GCP no está integrado en el catálogo.

### ✔ [gcp_vm_ociosa] VM con CPU ~5 % toda la semana → candidata a máquina más pequeña

```text
   Recurso: api-gce-ociosa · GCP · compute_instance · 5/5 métricas recientes · 168 h de historial
   → [recommendation · INFO · cost] Low CPU usage  (cpu_percent_oversized)
      CPU usage was usually 5.2% or lower over the last 7 days.
      Evidencia: valor=5.22 % · umbral=20 · ventana=7d · lecturas=168
      Por qué: 95% of hourly CPU peaks were at or below 5.2%. Highest peak: 5.2%. Based on 168 hourly readings over seven days.
      · Review a smaller instance or CPU tier.
      · Check memory, network, I/O, CPU credits, availability and licensing before resizing.
   → [suggestion · INFO · observability] Add memory and disk metrics  (capacity_visibility)
      Memory use and disk space are unavailable for this VM.
      Por qué: Review memory, disk and network use before choosing a smaller VM.
      · Add an agent to collect memory and disk usage.
      · Check the VM's network limit before resizing.
```

**Qué sucedió:**

Es la misma regla de `aws_ec2_ociosa`, con CPU ~5 % durante toda la semana. El motor usa exactamente las mismas condiciones en AWS y GCP (historial ≥ 135 h, p95 < 20 %, pico < 50 %, sin otra presión), así que genera **"Low CPU usage"**.

### ✔ [gcp_sql_sana] Cloud SQL Postgres sano (CPU 35 %, memoria 55 %, disco 45 %)

```text
   Recurso: orders-db · GCP · cloudsql_instance · 4/4 métricas recientes · 168 h de historial
   → Sin hallazgos (el recurso se ve sano o no se evalúa).
```

**Qué sucedió:**

Cloud SQL Postgres con sus 4 métricas: CPU 35 %, memoria 55 %, disco 45 % y conexiones. Ninguna supera 80 % (alerta) y ni la CPU ni el disco bajan de 20 % (ahorro), así que no hay hallazgos.

A diferencia de las VMs, Cloud SQL **sí** reporta memoria y disco por sí solo, por eso aquí no aparece la sugerencia del agente.

### ✔ [gcp_sql_ociosa] Cloud SQL con CPU ~6 % toda la semana → reducir CPU

```text
   Recurso: analytics-db · GCP · cloudsql_instance · 4/4 métricas recientes · 168 h de historial
   → [recommendation · INFO · cost] Low CPU usage  (cpu_percent_oversized)
      CPU usage was usually 6.3% or lower over the last 7 days.
      Evidencia: valor=6.26 % · umbral=20 · ventana=7d · lecturas=168
      Por qué: 95% of hourly CPU peaks were at or below 6.3%. Highest peak: 6.3%. Based on 168 hourly readings over seven days.
      · Review a smaller instance or CPU tier.
      · Check memory, network, I/O, CPU credits, availability and licensing before resizing.
```

**Qué sucedió:**

La CPU estuvo en ~6 % toda la semana, y la memoria (55 %) y el disco (45 %) están normales. Se cumplen las condiciones de ahorro de CPU y **no hay presión en otro recurso**, así que el motor recomienda revisar un tier de CPU más pequeño para la base de datos.

### ✔ [gcp_sql_cpu_baja_memoria_alta] CPU ~6 % pero memoria ~88 % → alerta de memoria y NO reducir

```text
   Recurso: cache-db · GCP · cloudsql_instance · 4/4 métricas recientes · 168 h de historial
   → [alert · WARNING · performance] High memory usage  (memory_percent_high)
      Memory usage stayed at 83.7% or higher for 15 minutes.
      Evidencia: valor=83.67 % · umbral=80 · ventana=15m · lecturas=4
      Por qué: The value shown is the lowest reading in this period.
      · Review memory use and check for leaks.
      · Check peak demand before changing capacity.
```

**Qué sucedió:**

La CPU está tan baja como en el caso anterior, pero la memoria se mantiene alrededor de 88 % (mínima de los últimos 15 min: 83,7 %). Pasan dos cosas:

1. **Alerta "High memory usage"** (WARNING, porque 83,7 % ≥ 80 % y < 90 %).
2. **No sale "Low CPU usage":** la regla de ahorro de CPU se bloquea si **cualquier otro recurso** de la máquina tiene presión ≥ 80 %. Reducir el tamaño de la instancia también reduce la memoria, y eso empeoraría el problema.

Lo cubre el test `test_cpu_cost_review_is_suppressed_by_recent_spikes_or_memory_pressure`.

### ✔ [gcp_sql_disco_sobrado] Cloud SQL de 100 GB usando ~5 GB toda la semana → reducir disco

```text
   Recurso: reports-db · GCP · cloudsql_instance · 4/4 métricas recientes · 168 h de historial
   → [recommendation · INFO · cost] Low storage usage  (filesystem_percent_oversized)
      Peak storage usage was 5.2% over the last 7 days. Latest usage: approximately 5.2 GB of 100 GB allocated.
      Evidencia: valor=5.24 % · umbral=20 · ventana=7d · lecturas=168
      Por qué: The value shown is the highest hourly storage reading. Based on 168 hourly readings over seven days.
      · Check data growth and backup retention before reducing storage.
      · Check Cloud SQL's minimum size and shrink support. Allow for downtime and lower I/O performance.
```

**Qué sucedió:**

La base tiene **100 GB** asignados (`dataDiskSizeGb` del inventario) y el uso máximo de la semana fue 5,2 %. Para recomendar reducir disco, el motor exige:

- Historial de disco completo (≥ 135 h). ✔
- **Pico máximo** de la semana < 20 % (no usa el percentil 95, porque aquí un solo pico sí importa). ✔

Con el tamaño asignado calcula el uso real, **~5,2 GB de 100 GB**, y genera **"Low storage usage"**. Las acciones advierten que Cloud SQL tiene un tamaño mínimo y que reducir disco puede requerir downtime.

### ✔ [gcp_sql_disco_llenandose] Disco subiendo 1 %/h hasta 84 % → alta ocupación y se llena en <24 h

```text
   Recurso: events-db · GCP · cloudsql_instance · 4/4 métricas recientes · 168 h de historial
   → [alert · WARNING · capacity] High storage usage  (filesystem_percent_high)
      Storage usage stayed at 83.0% or higher for 15 minutes.
      Evidencia: valor=83.0 % · umbral=80 · ventana=15m · lecturas=4
      Por qué: The value shown is the lowest reading in this period.
      · Review data retention and plan more storage.
      · Check peak demand before changing capacity.
   → [recommendation · WARNING · capacity] Storage may fill within 24 hours  (storage_exhaustion)
      Storage could be full in about 14.2 hours if growth continues at the current rate.
      Evidencia: valor=14.17 h · umbral=24 · ventana=24h · lecturas=25
      Por qué: Estimated from storage growth over the last 24 hours.
      · Check what is causing storage to grow.
      · Increase storage or remove data that is no longer needed after checking backups.
```

**Qué sucedió:**

El disco estuvo en 45 % casi toda la semana y en las últimas 24 h subió ~1 % por hora hasta llegar a 84 %. Se generan dos hallazgos:

1. **"High storage usage"** (alerta WARNING): la mínima de los últimos 15 min es 83 %, ≥ 80 %.
2. **"Storage may fill within 24 hours"** (recomendación): el motor ajusta una **regresión lineal** sobre las últimas 24 h de historial, y solo predice si:
   - el disco actual está entre 70 % y 100 %,
   - hay ≥ 18 lecturas en ≥ 18 h, sin huecos de más de 2 h,
   - la pendiente es positiva y el ajuste es convincente (R² ≥ 0,7).

   Con una pendiente de ~1 %/h y 84 % de uso actual, estima **~14 h** para llenarse. Si el crecimiento fuera errático (R² bajo) o el disco estuviera bajando, no haría la predicción.

### ✔ [gcp_sql_disco_critico] Disco al 95 % sostenido → alerta crítica

```text
   Recurso: legacy-db · GCP · cloudsql_instance · 4/4 métricas recientes · 168 h de historial
   → [alert · CRITICAL · capacity] Critical storage usage  (filesystem_percent_high)
      Storage usage stayed at 93.3% or higher for 15 minutes.
      Evidencia: valor=93.25 % · umbral=90 · ventana=15m · lecturas=4
      Por qué: The value shown is the lowest reading in this period.
      · Review data retention and plan more storage.
      · Check peak demand before changing capacity.
```

**Qué sucedió:**

El disco está al 95 % de forma sostenida (mínima de los últimos 15 min: 93,3 %), así que la alerta es **CRITICAL**. No hay predicción de llenado porque el historial está plano en ~95 % (pendiente ≈ 0): el disco está casi lleno, pero no está creciendo.

### ✔ [gcp_pubsub_topic_sano] Tema Pub/Sub con mensajes retenidos normales

```text
   Recurso: eventos · GCP · pubsub_topic · 1/1 métricas recientes · 168 h de historial
   → Sin hallazgos (el recurso se ve sano o no se evalúa).
```

**Qué sucedió:**

El tema de Pub/Sub tiene su única métrica (bytes retenidos) al día. El motor no tiene reglas de alerta para temas de Pub/Sub, solo verifica que haya datos, así que no hay hallazgos.

### ✔ [gcp_pubsub_sana] Suscripción Pub/Sub al día

```text
   Recurso: eventos-sub · GCP · pubsub_subscription · 2/2 métricas recientes · 168 h de historial
   → Sin hallazgos (el recurso se ve sano o no se evalúa).
```

**Qué sucedió:**

La suscripción tiene mensajes sin confirmar, pero el más viejo lleva ~15 s esperando, muy por debajo de los 300 s del umbral de warning. Es la misma regla que SQS: lo que importa es **cuánto espera** un mensaje, no cuántos hay.

### ✔ [gcp_pubsub_atascada] Suscripción con mensajes sin confirmar hace ~20 min → crítica

```text
   Recurso: eventos-batch · GCP · pubsub_subscription · 2/2 métricas recientes · 168 h de historial
   → [alert · CRITICAL · reliability] Messages are waiting too long  (oldest_message_seconds_high)
      Message wait time stayed at 1141.02 seconds or more for 15 minutes.
      Evidencia: valor=1141.02 s · umbral=900 · ventana=15m · lecturas=4
      Por qué: The value shown is the lowest reading in the last 15 minutes.
      · Check consumers, retries and the dead-letter queue.
      · Review the queue's delivery target and capacity.
```

**Qué sucedió:**

El mensaje sin confirmar más viejo lleva ~19 min (mínima: 1.141 s), por encima de los **900 s**, así que la alerta es **CRITICAL**. Usa la misma regla que `aws_sqs_atascada`: el motor trata igual las colas de AWS y GCP.

## Datos incompletos

### ✔ [historial_insuficiente] EC2 con CPU ~6 % pero solo 2 días de historial → NO recomienda, pide más datos

```text
   Recurso: web-recien-creada · AWS · ec2_instance · 5/5 métricas recientes · 48 h de historial
   → [suggestion · INFO · observability] Add memory and disk metrics  (capacity_visibility)
      Memory use and disk space are unavailable for this VM.
      Por qué: Review memory, disk and network use before choosing a smaller VM.
      · Add an agent to collect memory and disk usage.
      · Check the VM's network limit before resizing.
   → [suggestion · INFO · observability] Not enough usage history  (insufficient_history)
      At least seven days of CPU data are needed to review a smaller instance.
      Evidencia: valor=48 hours · umbral=135 · ventana=7d · lecturas=48
      Por qué: 48 hourly readings collected. The check needs at least 135 across the weekly window.
      · Keep collecting metrics for a full week, including scheduled jobs.
```

**Qué sucedió:**

La CPU está en ~6 %, igual que en `aws_ec2_ociosa`, pero solo hay **48 h** de historial. El motor necesita 135 lecturas por hora distribuidas en ~7 días, así que:

- **No** genera "Low CPU usage", aunque la CPU sea baja.
- Genera **"Not enough usage history"**, con la evidencia "48 de 135".

La razón es que en 2 días puede no haber pasado un job semanal o un pico de fin de semana, y recomendar reducir con ese historial sería arriesgado. Es lo que te pasa ahora con tus recursos reales: cuando se cumplan los 7 días, esta sugerencia se reemplaza por la recomendación real (o por nada, si la CPU no es baja).

### ✔ [sin_historial] VM recién conectada: métricas de ahora pero cero historial

```text
   Recurso: web-sin-historial · GCP · compute_instance · 5/5 métricas recientes · 0 h de historial
   → [suggestion · INFO · observability] Add memory and disk metrics  (capacity_visibility)
      Memory use and disk space are unavailable for this VM.
      Por qué: Review memory, disk and network use before choosing a smaller VM.
      · Add an agent to collect memory and disk usage.
      · Check the VM's network limit before resizing.
   → [suggestion · INFO · observability] Not enough usage history  (insufficient_history)
      At least seven days of CPU data are needed to review a smaller instance.
      Evidencia: valor=0 hours · umbral=135 · ventana=7d · lecturas=0
      Por qué: 0 hourly readings collected. The check needs at least 135 across the weekly window.
      · Keep collecting metrics for a full week, including scheduled jobs.
```

**Qué sucedió:**

La VM recién se conectó: hay métricas de los últimos minutos pero **0 horas de historial**. El motor puede evaluar alertas de presión, porque solo usan los últimos 15 min, pero no ninguna regla de ahorro. Sale "Not enough usage history" con "0 de 135".

Es lo primero que se ve al conectar una cuenta nueva.

### ✔ [historial_con_huecos] Semana completa pero con 1/3 de las horas perdidas → historial insuficiente

```text
   Recurso: web-con-huecos · AWS · ec2_instance · 5/5 métricas recientes · 112 h de historial
   → [suggestion · INFO · observability] Add memory and disk metrics  (capacity_visibility)
      Memory use and disk space are unavailable for this VM.
      Por qué: Review memory, disk and network use before choosing a smaller VM.
      · Add an agent to collect memory and disk usage.
      · Check the VM's network limit before resizing.
   → [suggestion · INFO · observability] Not enough usage history  (insufficient_history)
      At least seven days of CPU data are needed to review a smaller instance.
      Evidencia: valor=112 hours · umbral=135 · ventana=7d · lecturas=112
      Por qué: 112 hourly readings collected. The check needs at least 135 across the weekly window.
      · Keep collecting metrics for a full week, including scheduled jobs.
```

**Qué sucedió:**

Las 168 horas de la semana están representadas, pero falta 1 de cada 3 lecturas, así que quedan **112 horas**. El motor no solo mira cuántos días abarca el historial, también exige **cobertura ≥ 80 %** (135 de 168). Con 112 (~67 %) el historial no es confiable, porque los huecos podrían esconder justo los picos.

Esto pasa en la vida real cuando Vanguard o Prometheus estuvieron apagados parte de la semana, o cuando la sesión de AWS Academy se cerró y la EC2 se detuvo.

### ✔ [ec2_sin_disco_local] EC2 solo con EBS (como una t3.micro): sin métricas de disco local → NO es Missing metrics

```text
   Recurso: web-parcial · AWS · ec2_instance · 3/5 métricas recientes · 168 h de historial
   → [suggestion · INFO · observability] Add memory and disk metrics  (capacity_visibility)
      Memory use and disk space are unavailable for this VM.
      Por qué: Review memory, disk and network use before choosing a smaller VM.
      · Add an agent to collect memory and disk usage.
      · Check the VM's network limit before resizing.
```

**Qué sucedió:**

La EC2 tiene CPU y red, pero **no tiene lecturas de disco local** (`DiskReadBytes`/`DiskWriteBytes`). CloudWatch solo publica esas métricas para instancias con disco local (*instance store*). Una t3.micro, como la del lab, solo usa EBS, así que nunca llegarán. Las lecturas y escrituras de disco de esas instancias se ven en el **volumen EBS** (`VolumeReadBytes`/`VolumeWriteBytes`).

Por eso el catálogo marca esas dos métricas como **opcionales**: se muestran si llegan, pero su ausencia **no genera "Missing metrics"**. Antes de este ajuste, esta alerta aparecía en casi todas las EC2 modernas y nunca se resolvía.

### ✔ [metricas_faltantes_parcial] EC2 sin lecturas de CPU (métrica obligatoria) → Missing metrics

```text
   Recurso: web-sin-cpu · AWS · ec2_instance · 4/5 métricas recientes · 168 h de historial
   → [suggestion · WARNING · observability] Missing metrics  (missing_telemetry)
      No recent readings for: CPU.
      Evidencia: valor=2 metrics · umbral=None · ventana=20m · lecturas=0
      Por qué: 2 of 3 required metrics have recent readings.
      · Check the cloud connection and monitoring permissions.
      · Restore metric collection before reviewing capacity.
   → [suggestion · INFO · observability] Add memory and disk metrics  (capacity_visibility)
      Memory use and disk space are unavailable for this VM.
      Por qué: Review memory, disk and network use before choosing a smaller VM.
      · Add an agent to collect memory and disk usage.
      · Check the VM's network limit before resizing.
```

**Qué sucedió:**

La EC2 tiene red, pero **no tiene lecturas de CPU**. La CPU es una métrica **obligatoria**: CloudWatch la publica siempre para cualquier EC2 encendida, así que si falta, algo está mal (permisos, conexión o recolección). El motor genera **"Missing metrics"** con la lista de las que faltan, y el conteo solo considera las obligatorias ("2 of 3 required metrics"). Las de disco local no cuentan, porque son opcionales.

### ✔ [metricas_faltantes_total] Cola SQS sin ninguna lectura reciente (permisos o métricas caídas)

```text
   Recurso: pagos-sin-datos · AWS · sqs_queue · 0/2 métricas recientes · 168 h de historial
   → [suggestion · WARNING · observability] Missing metrics  (missing_telemetry)
      No recent readings for: Pending messages, Oldest message age.
      Evidencia: valor=0 metrics · umbral=None · ventana=20m · lecturas=0
      Por qué: 0 of 2 required metrics have recent readings.
      · Check the cloud connection and monitoring permissions.
      · Restore metric collection before reviewing capacity.
```

**Qué sucedió:**

La cola SQS existe en el inventario pero **no tiene ninguna lectura reciente** (0 de 2 métricas), y **ningún otro recurso de la misma cuenta** está reportando métricas. Eso no se explica por falta de tráfico: indica que la cuenta entera no está entregando datos. El motor genera "Missing metrics" y **no evalúa ninguna otra regla** para ese recurso, porque sin datos no puede saber si la cola está sana o atascada.

Causas reales típicas: faltan permisos de `cloudwatch:GetMetricData`, la conexión dio error, o la sesión de AWS Academy expiró.

### ✔ [sns_sin_trafico] Tema SNS que nadie usa, en una cuenta que sí reporta métricas → NO es Missing metrics

```text
   Recurso: RedshiftSNS · AWS · sns_topic · 0/2 métricas recientes · 0 h de historial
   → Sin hallazgos (el recurso se ve sano o no se evalúa).
```

**Qué sucedió:**

El tema SNS no tiene actividad, y CloudWatch **no publica métricas de SNS, SQS ni DynamoDB cuando no hay tráfico**. En la misma cuenta, una EC2 sí está reportando métricas al día. Eso prueba que la conexión y los permisos funcionan, así que el silencio del tema significa **"sin actividad"**, no "sin datos".

Para esto, el catálogo marca esas métricas como **de actividad** (`activity=True`). Solo se exigen cuando **toda la cuenta** está en silencio, y en este caso no lo está, así que no hay hallazgo. Antes de este ajuste, este era el falso positivo de `RedshiftSNS` en tu cuenta del Academy.

### ✔ [sns_cuenta_sin_datos] Tema SNS sin datos y ninguna otra métrica de la cuenta (permisos o conexión) → Missing metrics

```text
   Recurso: alertas-sin-datos · AWS · sns_topic · 0/2 métricas recientes · 0 h de historial
   → [suggestion · WARNING · observability] Missing metrics  (missing_telemetry)
      No recent readings for: Published messages, Failed deliveries.
      Evidencia: valor=0 metrics · umbral=None · ventana=20m · lecturas=0
      Por qué: 0 of 2 required metrics have recent readings.
      · Check the cloud connection and monitoring permissions.
      · Restore metric collection before reviewing capacity.
```

**Qué sucedió:**

El mismo tema sin datos, pero ahora **ninguna otra métrica de la cuenta** llega. En ese caso el silencio ya no se explica por falta de tráfico: lo más probable es que la conexión o los permisos estén fallando. El motor vuelve a exigir las métricas de actividad y genera **"Missing metrics"**.

Así, la mejora no oculta problemas reales: solo deja de alertar cuando hay evidencia de que la cuenta sí está reportando.

### ✔ [prometheus_caido] Prometheus no responde: el motor no inventa salud ni alertas

```text
   Recurso: web-prom-caido · AWS · ec2_instance · 5/5 métricas recientes · 168 h de historial
   → Sin hallazgos (el recurso se ve sano o no se evalúa).
```

**Qué sucedió:**

La CPU está en ~95 % (lo que normalmente daría una alerta crítica), pero se simula que **Prometheus no responde** (`telemetry_available=False`). El motor **no genera ningún hallazgo** para ese recurso: ni la alerta crítica ni la sugerencia del agente.

Que no haya hallazgos **no significa que el recurso esté sano**. En la app, el resumen marca la telemetría como no disponible y el panel muestra *"Metrics are unavailable"*, para no confundir "sin alertas" con "todo bien". Además, las recomendaciones que ya estaban abiertas no se cierran durante la caída. Lo cubren los tests `test_prometheus_outage_is_visible_and_does_not_fake_health` y `test_history_outage_preserves_live_alerts`.

### ✔ [recurso_apagado] EC2 detenida: se ignora aunque tenga métricas

```text
   Recurso: web-apagada · AWS · ec2_instance · 5/5 métricas recientes · 168 h de historial
   → Sin hallazgos (el recurso se ve sano o no se evalúa).
```

**Qué sucedió:**

La EC2 tiene estado `stopped`. El motor ignora los recursos en estados inactivos (`stopped`, `terminated`, `suspended`, `deleting`, etc.) aunque tengan métricas, porque recomendar reducir el tamaño o alertar por CPU en una máquina apagada no tiene sentido.

En tu caso real, cuando la sesión de AWS Academy se cierra y la EC2 se detiene, sus recomendaciones dejan de evaluarse hasta que vuelva a estar `running`.

## Inventario: discos, IP y EC2 detenidas

### ✔ [aws_ec2_detenida_con_ebs] EC2 detenida desde hace semanas con un EBS de 100 GB adjunto (sigue cobrando)

```text
   Recurso: batch-detenida · AWS · ec2_instance · 0/5 métricas recientes · 0 h de historial
   → [recommendation · INFO · cost] Stopped instance with billed storage  (stopped_instance_storage)
      This stopped instance keeps 1 EBS volume(s) (100 GB) that are still billed.
      Evidencia: valor=100 GB · umbral=None · ventana=inventory · lecturas=0
      Por qué: Attached volumes: ebs_volume-17. User initiated (2026-09-30 18:02:11 GMT).
      · Confirm the owner and whether the instance will be started again.
      · Take a snapshot before terminating the instance or deleting its volumes.
```

**Qué sucedió:**

La instancia está `stopped`. AWS no cobra el cómputo de una EC2 detenida, pero **sí sigue cobrando sus volúmenes EBS**, que permanecen adjuntos. La regla `stopped_instance_storage` usa solo el inventario:

1. Estado `stopped` e inventario reciente (visto en los últimos 30 min).
2. Toma los volúmenes de `BlockDeviceMappings` que existen en el inventario y suma su `Size` (aquí 100 GB).
3. Si AWS lo informa, añade el motivo y la fecha de detención (`StateTransitionReason`).

No se duplica con `unattached_volume`, porque el volumen sigue adjunto. Además, sus volúmenes ya no generan "Missing metrics": AWS no publica métricas de EBS mientras la instancia está detenida.

En la app, si el costo por recurso está disponible, se muestra el **ahorro estimado** como la suma del costo diario de los volúmenes × 30. En AWS Academy ese dato no existe, así que el costo aparece como **no atribuible**, con el motivo.

### ✔ [gcp_disco_sin_adjuntar] Persistent Disk de 200 GB sin ninguna VM que lo use

```text
   Recurso: old-data-disk · GCP · persistent_disk · 0/0 métricas recientes · 0 h de historial
   → [recommendation · INFO · cost] Unattached volume  (unattached_volume)
      This volume is not attached to an instance and may still incur storage charges.
      Evidencia: valor=0 attachments · umbral=None · ventana=inventory · lecturas=0
      Por qué: No attached instances in the latest inventory. Provisioned size: 200 GB.
      · Check whether the volume is still needed.
      · Confirm ownership and backups before taking a snapshot or deleting it.
```

**Qué sucedió:**

Es la misma regla que `aws_ebs_suelto` (`unattached_volume`), ahora para GCP. Se activa con un disco en estado `READY` y una lista `users` **vacía**, es decir, ninguna VM lo usa. Si `users` falta (dato incompleto), no se concluye nada. Se añade el tamaño aprovisionado a la explicación.

La facturación de GCP sí trae el costo por disco (`.../zones/<zona>/disk/<id>`). Por eso, en la app, este hallazgo muestra el **costo observado** y el **ahorro estimado** con su fórmula y nivel de confianza.

### ✔ [gcp_ip_sin_uso] IP externa reservada que ningún recurso usa

```text
   Recurso: spare-ip · GCP · ip_address · 0/0 métricas recientes · 0 h de historial
   → [recommendation · INFO · cost] Unused external IP address  (unused_address)
      This reserved external IP address is not used by any resource and may still be billed.
      Evidencia: valor=0 resources · umbral=None · ventana=inventory · lecturas=0
      Por qué: Address 34.1.2.3 is reserved and has no users in the latest inventory.
      · Check DNS records, firewall allowlists and failover plans that use this address.
      · Release the address only after confirming it is no longer needed.
```

**Qué sucedió:**

Regla `unused_address`: dirección `EXTERNAL`, estado `RESERVED` y sin `users`. GCP cobra las IP externas reservadas que no se usan. Las acciones piden revisar primero DNS, allowlists y planes de failover antes de liberarla, porque una IP liberada no se recupera.

La facturación de GCP trae el costo por dirección (`.../regions/<región>/addresses/<id>`), así que también puede mostrar el ahorro estimado.

### ✔ [gcp_ip_en_uso] IP externa usada por un balanceador → NO se recomienda liberar

```text
   Recurso: lb-ip · GCP · ip_address · 0/0 métricas recientes · 0 h de historial
   → Sin hallazgos (el recurso se ve sano o no se evalúa).
```

**Qué sucedió:**

La IP está `IN_USE` y tiene un usuario (una forwarding rule de un balanceador). No es un desperdicio, así que no hay hallazgo. Este escenario asegura que la regla no recomiende liberar una IP en uso.

## Observaciones generales

- **"Add memory and disk metrics" aparece en todas las VMs**, incluso con datos completos: el catálogo de Vanguard todavía no lee memoria ni disco de VMs (CloudWatch Agent / Ops Agent). Para que desaparezca habría que añadir esas métricas al catálogo.
- **Las reglas de ahorro solo existen para CPU (VMs y Cloud SQL) y disco (Cloud SQL), más el EBS suelto.** DynamoDB y los temas de Pub/Sub solo verifican que lleguen métricas.
- **Ya no hay falsos positivos de métricas faltantes** en EC2 solo con EBS (`ec2_sin_disco_local`) ni en SNS, SQS o DynamoDB sin tráfico (`sns_sin_trafico`). "Missing metrics" solo aparece cuando falta una métrica obligatoria, o cuando toda la cuenta está en silencio (`metricas_faltantes_total`, `sns_cuenta_sin_datos`).
- **Las mismas reglas aplican a AWS y GCP:** el motor no depende del proveedor, sino del tipo de métrica.
