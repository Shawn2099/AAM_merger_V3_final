.PHONY: dev lint format test business-logic

dev:
	UV_CACHE_DIR=/tmp/uv-cache uv sync --all-groups

lint:
	UV_CACHE_DIR=/tmp/uv-cache uv run ruff check .
	UV_CACHE_DIR=/tmp/uv-cache uv run ruff format --check .

format:
	UV_CACHE_DIR=/tmp/uv-cache uv run ruff format .

test:
	UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q

# Engine verdict + what the operator sees, for every verdict the product can
# produce (merge, needs review, all quarantine reasons, customs, holding area).
# This is the slice to run before any delivery or after touching reconciliation.
business-logic:
	UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q \
		tests/test_business_scenarios_ui.py \
		tests/test_reconciliation.py \
		tests/test_edge_reconciliation.py \
		tests/test_matching.py \
		tests/test_grouping.py \
		tests/test_merge.py \
		tests/test_customs.py \
		tests/test_quarantine.py \
		tests/test_dashboard.py \
		tests/test_best_case_pipeline.py \
		tests/test_untested_engine_paths.py

prefect-pool:
	prefect work-pool create aam-merger-process-pool --type process || true
	prefect work-pool ls

deploy-prefect:
	UV_CACHE_DIR=/tmp/uv-cache uv run python scripts/deploy_prefect.py

backup:
	UV_CACHE_DIR=/tmp/uv-cache uv run python scripts/backup_db.py

prefect:
	NO_PROXY=127.0.0.1,localhost,::1 prefect server start --host 127.0.0.1 --port 4200 &
	NO_PROXY=127.0.0.1,localhost,::1 prefect worker start --pool aam-merger-process-pool &
	uv run python scripts/deploy_prefect.py

prefect-stop:
	pkill -f "prefect server" || true
	pkill -f "prefect worker" || true

prefect-logs:
	tail -f /tmp/prefect-server.log /tmp/prefect-worker.log

