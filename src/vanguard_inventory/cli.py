from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .config import load_environment
from .database import check_database, save_resources
from .serialization import write_snapshot


def _csv(value: str | None) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inventario multi-cloud de Vanguard")
    subparsers = parser.add_subparsers(dest="command", required=True)

    collect = subparsers.add_parser("collect", help="Recolectar recursos")
    collect.add_argument("provider", choices=("gcp", "aws"))
    collect.add_argument("--project-id", default=os.getenv("GCP_PROJECT_ID"))
    collect.add_argument("--profile", default=os.getenv("AWS_PROFILE") or None)
    collect.add_argument(
        "--regions",
        default=(
            os.getenv("AWS_REGIONS")
            or os.getenv("AWS_DEFAULT_REGION")
            or os.getenv("AWS_REGION", "")
        ),
    )
    collect.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    collect.add_argument("--output-dir", type=Path, default=Path("snapshots"))
    collect.add_argument("--no-snapshot", action="store_true")

    check = subparsers.add_parser("check", help="Validar credenciales y conectividad")
    check.add_argument("provider", choices=("gcp", "aws", "database"))
    check.add_argument("--project-id", default=os.getenv("GCP_PROJECT_ID"))
    check.add_argument("--profile", default=os.getenv("AWS_PROFILE") or None)
    check.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    return parser


def _collect(args: argparse.Namespace) -> int:
    if args.provider == "gcp":
        if not args.project_id:
            raise ValueError("GCP_PROJECT_ID o --project-id es obligatorio")
        from .collectors.gcp import GCPCollector
        result = GCPCollector(args.project_id).collect()
    else:
        from .collectors.aws import AWSCollector
        result = AWSCollector(args.profile, _csv(args.regions)).collect()

    print(f"Recursos recolectados: {len(result.resources)}")
    if not args.no_snapshot:
        path = write_snapshot(result, args.provider, args.output_dir)
        print(f"Snapshot: {path}")
    if args.database_url:
        saved = save_resources(args.database_url, result.resources)
        print(f"Recursos guardados/actualizados en PostgreSQL: {saved}")
    else:
        print("PostgreSQL omitido: DATABASE_URL no está configurada")
    for error in result.errors:
        print(f"ERROR: {error}", file=sys.stderr)
    return 2 if result.errors else 0


def _check(args: argparse.Namespace) -> int:
    if args.provider == "gcp":
        if not args.project_id:
            raise ValueError("GCP_PROJECT_ID o --project-id es obligatorio")
        from .collectors.gcp import check_gcp
        print(check_gcp(args.project_id))
    elif args.provider == "aws":
        from .collectors.aws import check_aws
        print(check_aws(args.profile))
    else:
        if not args.database_url:
            raise ValueError("DATABASE_URL o --database-url es obligatorio")
        print(check_database(args.database_url))
    return 0


def main() -> None:
    load_environment()
    args = build_parser().parse_args()
    try:
        code = _collect(args) if args.command == "collect" else _check(args)
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        code = 1
    raise SystemExit(code)


if __name__ == "__main__":
    main()
