# Estructura del proyecto

El backend usa el paquete `vanguard`. Inventario es un dominio dentro de la
aplicación, junto con cuentas, empresas, conexiones, costos, utilización,
recomendaciones y laboratorios.

```text
backend/
  src/vanguard/
    api.py                 # Entrada FastAPI, dependencias y composición de rutas
    core/                  # Configuración, seguridad HTTP y serialización
    accounts/              # Usuarios, autenticación, sesiones y correo
    organizations/         # Empresas, miembros y roles
    connections/           # Conexiones cloud y protección de credenciales
    inventory/             # Recursos, persistencia, taxonomía y sincronización
      collectors/          # Descubrimiento de recursos AWS y GCP
    costs/                 # Facturación, fuentes y recolección de costos
    utilization/           # Métricas cloud, OTLP y consultas a Prometheus
    recommendations/       # Evaluación de capacidad y sobreaprovisionamiento
    labs/                  # Ejecución de laboratorios Terraform
  tests/
frontend/
  src/
    api/                   # Cliente HTTP común
    auth/                  # Sesión y pantallas de autenticación
    accounts/              # Configuración de cuenta y conexiones
    organizations/         # Administración de empresas
    dashboard/
    inventory/
    costs/
    utilization/
    recommendations/
    shared/
      components/          # Componentes reutilizables
      layout/
      resources/           # Nombres y agrupaciones usados por varias pantallas
      styles/
      utils/
  tests/
infrastructure/
  lab/                     # Módulo Terraform y bloqueo de versiones
  policies/                # Permisos de lectura AWS y GCP
observability/             # Configuración de OpenTelemetry y Prometheus
docs/
```

Los contratos de entrada y salida viven en `schemas.py` dentro de su dominio.
Los recolectores de inventario están separados de la recolección de métricas y
costos. `core` contiene utilidades comunes; no debe depender de los dominios.
En el frontend, un catálogo usado por varias pantallas pertenece a `shared`,
mientras las llamadas de inventario siguen en `inventory/api.js`.

## Ejecución y validación

`make dev`, `make stop` y `make status` usan `dev.sh`, que ya apunta al nuevo
paquete. Para ejecutar únicamente la API desde la raíz:

```sh
.venv/bin/python -m uvicorn vanguard.api:app --app-dir backend/src --reload --host 127.0.0.1 --port 8000
```

`make verify` compila Python, ejecuta las pruebas del backend y del cliente HTTP,
y construye el frontend. `make lint` revisa Python con Ruff.
Las pruebas de integración con PostgreSQL se activan con
`VANGUARD_TEST_DATABASE_URL` apuntando a una instancia desechable.

La reorganización conserva las rutas HTTP, sus contratos, las tablas y las
claves de métricas. Las rutas internas de Python ahora empiezan por `vanguard`;
Terraform se encuentra en `infrastructure/lab`.
