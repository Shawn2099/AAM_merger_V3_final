.PHONY: dev lint format test

dev:
	UV_CACHE_DIR=/tmp/uv-cache uv sync --all-groups

lint:
	UV_CACHE_DIR=/tmp/uv-cache uv run ruff check .
	UV_CACHE_DIR=/tmp/uv-cache uv run ruff format --check .

format:
	UV_CACHE_DIR=/tmp/uv-cache uv run ruff format .

test:
	UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q

prefect-pool:
	prefect work-pool create aam-merger-process-pool --type process || true
	prefect work-pool ls

deploy-prefect:
	UV_CACHE_DIR=/tmp/uv-cache uv run python scripts/deploy_prefect.py

backup:
	mkdir -p ./data/backup
	sqlite3 ./data/aam_merger.db "PRAGMA wal_checkpoint(TRUNCATE);" 2>/dev/null || true
	cp ./data/aam_merger.db ./data/backup/aam_merger_$$(date +%Y%m%d_%H%M%S).db 2>/dev/null || echo "no db yet"

prefect:
	NO_PROXY=127.0.0.1,localhost,::1 prefect server start --host 127.0.0.1 --port 4200 &
	NO_PROXY=127.0.0.1,localhost,::1 prefect worker start --pool aam-merger-process-pool &
	uv run python scripts/deploy_prefect.py

prefect-stop:
	pkill -f "prefect server" || true
	pkill -f "prefect worker" || true

prefect-logs:
	tail -f /tmp/prefect-server.log /tmp/prefect-worker.log

