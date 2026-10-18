# Revisión de seguridad, empresas y escalabilidad

Revisión del 18 de octubre de 2026.

## Modelo de empresas y permisos

Una cuenta puede pertenecer a varias organizaciones. Cada sesión tiene una organización activa, validada contra `organization_members` en cada petición. Inventario, conexiones, costes, métricas, recomendaciones y laboratorios se filtran por esa organización.

| Operación | Propietario | Administrador | Miembro | Lector |
| --- | --- | --- | --- | --- |
| Consultar inventario, utilización, costes y recomendaciones | Sí | Sí | Sí | Sí |
| Sincronizar costes | Sí | Sí | Sí | No |
| Gestionar conexiones y fuentes de facturación | Sí | Sí | No | No |
| Crear/destruir laboratorios | Sí | Sí | No | No |
| Añadir miembros, cambiar roles y retirar miembros | Sí | Sí | No | No |
| Cambiar perfil y contraseña personales | Sí | Sí | Sí | Sí |

La propiedad no se puede asignar, retirar ni degradar desde la gestión de miembros. Los cambios de rol y la retirada de una membresía cierran las sesiones activas del usuario en esa empresa. Para añadir un miembro, debe existir previamente una cuenta con su email; no se envían invitaciones por correo.

El navegador incluye `X-Organization-ID` en las peticiones autenticadas, además de `X-CSRF-Token` en escrituras. El servidor rechaza una organización que no coincide con la sesión. Así una pestaña antigua no recibe datos ni ejecuta operaciones bajo la empresa elegida en otra pestaña. Cambiar de organización verifica membresía, rota CSRF y reinicia los componentes de la vista. Las respuestas tardías de una sesión anterior no invalidan una sesión nueva.

## Migración compatible

Al iniciar la API se crea una organización inicial para cada usuario existente, conservando su UUID y vinculándolo como propietario. Sus conexiones, inventario, costes, laboratorios y sesiones existentes siguen asociados al mismo identificador.

Las columnas y etiquetas históricas `owner_id` se conservan por compatibilidad con PostgreSQL y los 15 días de métricas de Prometheus. **A partir de esta migración representan el ID de la organización, no el ID del usuario que hace la petición.** Las claves foráneas ahora apuntan a `organizations`. Claves compuestas `(owner_id, connection_id)` impiden escribir recursos/costes/laboratorios con conexiones de otra empresa.

Las nuevas restricciones se incorporan con `NOT VALID`: protegen nuevas escrituras sin escanear inmediatamente todo el historial. Los registros antiguos incompletos no se reasignan a otras empresas. Antes de una actualización de producción, conservar un respaldo y ejecutar el arranque/migración en una ventana de mantenimiento; el esquema todavía se inicializa desde el proceso de arranque, no mediante una herramienta independiente de migraciones.

Las credenciales nuevas se cifran con un sobre que incluye la organización. Intentar descifrarlas para otra organización falla. Los cifrados antiguos se siguen leyendo para mantener compatibilidad y reciben el nuevo formato cuando un administrador vuelve a guardar las credenciales. Se conserva el estado de Terraform existente.

## Correcciones aplicadas

- Consultas de Prometheus con una sola organización obligatoria; se rechazan conjuntos sin identidad o con identidades mezcladas. Las dimensiones compartidas reducen el volumen consultado.
- Helpers de conexiones de la API requieren organización explícita. La consulta global de credenciales se limita a una función identificada para los recolectores internos; se eliminó el patrón de una consulta adicional por conexión.
- Validación del propietario/proveedor/cuenta/región antes de persistir inventario y del origen antes de guardar costes.
- Validación de `token_uri` y `universe_domain` en credenciales GCP para evitar endpoints arbitrarios; se eliminó el fallback a credenciales del servidor en el recolector GCP. Google exige validar configuraciones recibidas de fuentes externas: [guía oficial de autenticación](https://github.com/googleapis/google-auth-library-python/blob/main/docs/user-guide.rst).
- Respuestas API con `Cache-Control: no-store` y cabeceras básicas; orígenes de escritura permitidos explícitamente; límite real de 256 KiB para cuerpos, incluso fragmentados. Los errores de validación ya no incluyen el input que puede contener secretos.
- Contadores atómicos en PostgreSQL para intentos de login y límites para registro/recuperación. Las cuentas inexistentes también ejecutan scrypt. Estos límites se comparten entre procesos de API.
- Errores de proveedores/Terraform expuestos de forma genérica; los subprocesos de Terraform reciben un entorno reducido, sin la clave maestra ni la URL de PostgreSQL. Se conservaron los estados parciales y se añadieron límites de tiempo.
- Conexiones DB, RPC cloud, BigQuery y comandos externos con límites de espera. El bucle de trabajos BigQuery ya no espera indefinidamente.
- Actualización compatible de `source-map-js` 1.2.1 a 1.2.2 para corregir [GHSA-68fv-2mgg-jv7q](https://github.com/advisories/GHSA-68fv-2mgg-jv7q).

## Mejoras de rendimiento y limpieza

- Se eliminó la ejecución de DDL de altas, sesiones, operaciones de conexiones, despliegues y ciclos habituales de recolección. El arranque prepara el esquema.
- Utilización se pagina en SQL y proyecta sólo las columnas necesarias; ya no descarga todo el inventario y sus documentos cloud para mostrar una página.
- Recomendaciones carga sólo la pequeña parte del documento original que necesitan sus reglas, además de las identidades/atributos. El historial sigue acotado a 168 máximos horarios por serie.
- Inventario utiliza `executemany` para escrituras y claves de snapshots, aprovechando el procesamiento en lote de psycopg.
- Métricas agrupa recursos por organización/conexión una vez, eliminando el cruce completo recursos × conexiones, y recolecta hasta cuatro conexiones concurrentes.
- Los locks locales de inventario dejan de acumularse tras eliminar conexiones. Los claims de inventario y costes siguen siendo atómicos en PostgreSQL y ahora incluyen organización.
- El frontend cancela comprobaciones anteriores de conexiones y evita refrescarlas por cada navegación. Cambiar de empresa limpia estado y solicitudes de la vista anterior.
- Ruff ordena imports y verifica código no usado/errores básicos. Se añadieron dependencias de desarrollo y comandos de lint/auditoría separados de las dependencias de producción.

## Configuración de producción

Configurar `VANGUARD_ENVIRONMENT=production`, un `VANGUARD_MASTER_KEY` Fernet válido desde un gestor de secretos, `VANGUARD_SECURE_COOKIES=true` y `VANGUARD_PUBLIC_URL=https://...`. El arranque rechaza combinaciones inseguras y tokens de recuperación expuestos. `VANGUARD_ALLOWED_ORIGINS` admite una lista separada por comas para frontends autorizados. Usar HTTPS, PostgreSQL con privilegios mínimos y observabilidad en una red privada; Prometheus contiene series de todas las empresas y sólo debe consultarse mediante la API autorizada.

El modelo protege el acceso de la aplicación y la coherencia de sus escrituras. No incorpora Row Level Security ni aislamiento físico entre empresas. No se han realizado pruebas de carga que acrediten una capacidad máxima. La recolección periódica y su estado en memoria siguen ligados al proceso de la API; antes de desplegar varias réplicas con recolectores activos conviene separar esos trabajos en un worker único y compartir su estado. Tampoco incluye todavía SSO, transferencia de propiedad o auditoría persistente de todas las acciones.

## Verificación reproducible

Instalar herramientas con `.venv/bin/python -m pip install -r requirements-dev.txt`.

- `make verify`: compilación Python, pruebas backend, pruebas del cliente HTTP y build React.
- `make lint`: comprobación Ruff del backend.
- `make security-audit`: vulnerabilidades Python y npm.
- `VANGUARD_TEST_DATABASE_URL='...' make verify`: añade pruebas con una base PostgreSQL **desechable**. Crean y eliminan sus propios esquemas, prueban el modelo nuevo y la migración desde tablas con propietarios por usuario, sesiones, permisos, acceso cruzado y claves foráneas.
- `VANGUARD_PROMTOOL=/ruta/a/promtool make verify`: añade la prueba de compatibilidad de las consultas históricas con Prometheus.

Se comprobó visualmente la creación de organizaciones, altas de miembros registrados, cambios de rol, cambio de empresa con estado limpio, controles de lector, tema oscuro y diseño móvil mediante datos de prueba temporales. Las pruebas de permisos y migración usan PostgreSQL 16 real en una instancia temporal; no se modificó la base de datos del proyecto ni se ejecutaron despliegues cloud.

Resultado de esta revisión: 61 pruebas de backend y 4 del cliente aprobadas, incluyendo 14 pruebas contra PostgreSQL real; una prueba opcional de Prometheus se omitió por no tener `VANGUARD_PROMTOOL` configurado en esta ejecución. Ruff, compilación Python y build React correctos. La auditoría de los 49 paquetes Python instalados y la auditoría npm finalizaron con cero vulnerabilidades conocidas tras actualizar la dependencia afectada. Estos resultados son una fotografía de las versiones revisadas, no una garantía de ausencia de vulnerabilidades futuras.
