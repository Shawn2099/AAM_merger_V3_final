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
	cp ./data/aam_merger.db ./data/backup/aam_merger_$$(date +%Y%m%d_%H%M%S).db 2>/dev/null || echo "no db yet"

