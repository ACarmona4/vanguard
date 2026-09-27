.PHONY: dev stop status observability observability-stop verify aws-lab-plan aws-lab

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
	@npm --prefix frontend run build

aws-lab-plan:
	@.venv/bin/python infraestructure/scripts/aws_lab.py plan

aws-lab:
	@.venv/bin/python infraestructure/scripts/aws_lab.py apply
