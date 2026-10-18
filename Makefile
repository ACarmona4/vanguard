.PHONY: dev stop status observability observability-stop verify lint security-audit

dev:
	@./dev.sh start

stop:
	@./dev.sh stop

status:
	@./dev.sh status

observability:
	@docker compose up -d otel-collector prometheus

observability-stop:
	@docker compose stop otel-collector prometheus

verify:
	@PYTHONPATH=backend/src .venv/bin/python -m compileall -q backend/src
	@PYTHONPATH=backend/src .venv/bin/python -m unittest discover -s backend/tests
	@npm --prefix frontend test
	@npm --prefix frontend run build

lint:
	@.venv/bin/python -m ruff check backend

security-audit:
	@.venv/bin/python -m pip_audit
	@npm --prefix frontend audit
