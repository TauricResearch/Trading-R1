# Trading-R1: development entrypoints. `make help` lists targets.
.DEFAULT_GOAL := help
.PHONY: help dev lint smoke

help:  ## list targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | sed 's/:.*##/\t/' | sort

dev:  ## run the service with auto-reload
	uv run trading-r1 serve --reload

lint:  ## ruff and Biome
	uv run ruff check .
	uv run ruff format --check .
	cd tui && bunx @biomejs/biome@2.6.4 format .

smoke:  ## the command runs, the backend answers, the terminal UI typechecks
	uv run trading-r1 --version
	for c in serve export sessions channels jobs secrets daemon sync; do \
	  uv run trading-r1 $$c --help > /dev/null || exit 1; done
	home=$$(mktemp -d); port=$$(uv run python -c "import socket; s = socket.socket(); s.bind(('127.0.0.1', 0)); print(s.getsockname()[1])"); \
	TR1_HOME=$$home TR1_PORT=$$port uv run trading-r1 serve & pid=$$!; \
	ok=1; for i in $$(seq 1 30); do \
	  kill -0 $$pid 2>/dev/null || break; \
	  curl -sf http://127.0.0.1:$$port/livez > /dev/null && { ok=0; break; }; sleep 1; done; \
	kill $$pid 2>/dev/null; wait $$pid 2>/dev/null; exit $$ok
	cd tui && bun install --frozen-lockfile && bunx tsc --noEmit
