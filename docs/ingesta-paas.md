# Ingesta PaaS (fase 1)

Solo ingesta: inventario, métricas, historial, permisos y cruce con la facturación. **No se agregaron reglas ni recomendaciones**: el motor se comporta igual (demo 40/40). Base: la propuesta técnica de ingesta PaaS aprobada, con S3 incluido y retención de 35 días.

## Qué se recolecta ahora

| Recurso | Nuevo en el inventario (`attributes`) | Métricas nuevas | Historial (7 días por hora) |
|---|---|---|---|
| **SQS** | `dead_letter_queue`, `long_polling_seconds`, `retention_seconds`, `visibility_timeout_seconds`, `fifo`, `tags` | `messages_sent_per_second`, `messages_received_per_second`, `empty_receives_per_second` (de actividad) | Mensajes enviados y recibidos |
| **SNS** | `subscriptions`, `subscriptions_pending`, `fifo`, `tags` | — | Publicados |
| **DynamoDB** | `billing_mode` (normalizado: sin resumen = `PROVISIONED`), `provisioned_read`, `provisioned_write`, `table_size_bytes`, `tags` | `provisioned_read_units`, `provisioned_write_units` (opcionales: solo tablas *provisioned*) | Lecturas y escrituras consumidas |
| **Cloud SQL** | `tier`, `availability_type`, `disk_size_gb`, `backups_enabled`, `labels` | — | Memoria y conexiones |
| **Pub/Sub Topic** | `subscriptions` (cantidad), `retention_seconds`, `labels` | `published_per_second` (de actividad) | Publicados |
| **Pub/Sub Subscription** | `retention_seconds`, `retain_acked_messages`, `push`, `expiration_ttl_seconds`, `labels` | `acked_per_second` (de actividad) | Mensajes confirmados |
| **Cloud Storage** *(nuevo tipo `storage_bucket`)* | `location_type`, `storage_class`, `autoclass`, `lifecycle_rules`, `lifecycle_actions`, `soft_delete_seconds`, `versioning`, `created_at`, `labels` | — | — |
| **S3** *(nuevo tipo `s3_bucket`)* | `lifecycle_rules`, `aborts_incomplete_uploads`, `versioning`, `intelligent_tiering`, `created_at`, `tags` | — | — |

- El `raw_data` guarda también la respuesta completa del proveedor.
- Los buckets pertenecen al grupo **Storage** (filtros de Inventory).
- **Facturación:** `//storage.googleapis.com/projects/_/buckets/<nombre>` se cruza con `storage_bucket`. Verificado con la facturación real: el bucket `…-tfstate` tiene 16 días facturados. S3 no tiene costo por bucket en Cost Explorer.

## Diseño
- **Degradación:** las llamadas de detalle (atributos, etiquetas, *lifecycle*, suscripciones) son opcionales. Si falta un permiso, el recurso **se inventaría igual**, sin ese dato (`null`, `{}` o `0`). Un servicio deshabilitado no rompe el resto.
- **Métricas de actividad:** las de tráfico (SQS, Pub/Sub) no generan "Missing metrics" si la cuenta reporta otras métricas. Las de capacidad *provisioned* de DynamoDB son opcionales, porque las tablas *on-demand* no las tienen.
- **Sin dependencias nuevas:** `boto3` para AWS; la API REST con `AuthorizedSession` para Cloud Storage; el cliente de Pub/Sub existente para las suscripciones.

## Configuración
- `compose.yaml`: Prometheus `--storage.tsdb.retention.time=35d` (antes 15d). Prometheus lo muestra como `5w`. Para aplicarlo:
  ```bash
  docker compose up -d prometheus
  ```
- **Permisos AWS** (`aws-inventory-readonly.json`): `sqs:GetQueueAttributes`, `sqs:ListQueueTags`, `sns:GetTopicAttributes`, `sns:ListTagsForResource`, `dynamodb:ListTagsOfResource`, `s3:ListAllMyBuckets`, `s3:GetBucketLocation`, `s3:GetLifecycleConfiguration`, `s3:GetBucketVersioning`, `s3:GetBucketTagging`, `s3:GetIntelligentTieringConfiguration`.
- **Permisos GCP** (`gcp-inventory-role.yaml`): `storage.buckets.list`, `pubsub.topics.listSubscriptions`.

## Pruebas
- `test_aws_collector.py` (nuevo):
  - SQS con DLQ, *long polling* y etiquetas, y una cola sin permiso de atributos que se inventaría igual;
  - SNS con suscripciones y sin permiso de etiquetas;
  - DynamoDB *provisioned* frente a *on-demand*;
  - S3 con *lifecycle* (incluida la regla de cargas incompletas), región por defecto `us-east-1` y degradación sin permisos.
- `test_gcp_collector.py`: buckets (multirregión con *lifecycle*, *soft delete* y versionado; región con Autoclass; nombres con puntos) y temas con conteo de suscripciones, incluido el caso sin permiso.
- `test_utilization_catalog.py`: las métricas nuevas son de actividad u opcionales; los buckets están en Storage y no tienen métricas.
- `test_recommendations.py`: una tabla *on-demand* sin métricas de capacidad no genera "Missing metrics".
- `test_recommendation_economics.py`: cruce de facturación de buckets.
- Resultado: **113 tests de backend OK**, `ruff` sin errores, frontend OK, demo 40/40. Se ajustaron dos casos que dependían del comportamiento anterior: un nombre de bucket que antes no debía cruzarse, y el escenario de cola sin datos, que ahora quita todas sus métricas.

## Verificación con datos reales (GCP)

| Recurso | Lo que llegó |
|---|---|
| Bucket `…-tfstate` | Región, `STANDARD`, versionado, *soft delete* 7 días, sin *lifecycle* |
| Tema `demo-dev-user-events` | 2 suscripciones |
| Suscripciones | Retención 7 días, *pull* |

Las métricas de publicados y confirmados de Pub/Sub no aparecen porque el tema no tiene tráfico (comportamiento esperado de las métricas de actividad). La colección no dio errores en GCP. La parte de AWS no se pudo probar en vivo porque la sesión de AWS Academy había expirado.

## Siguiente paso
- Reglas sobre estos datos (fase de recomendaciones PaaS).
- **Ingesta 2:** RDS / Aurora.
