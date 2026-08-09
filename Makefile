# Epimemer test targets.
#
#   make test              default suite — embedded, no external services
#   make test-frontend     visualization frontend: vitest + tsc type-check
#   make bench             scaling measurements (see dev-docs/BENCHMARKS.md)
#   make test-integration  opt-in SurrealDB suites (real ws:// connection +
#                          cross-connection concurrency, and on-disk durability
#                          across a server restart) that mem:// can't cover.
#                          Requires Docker; spins up throwaway servers, waits
#                          for them, runs the suites, and always tears them down.
#                          Refuses to start if the port is taken — re-run as
#                          `make test-integration SURREAL_PORT=8123`.
#
# `make test` stays Python-only and deliberately does not chain the frontend
# suite: Node is not a prerequisite for working on the backend, and a missing
# npm should not make the Python suite look broken. Run both before touching
# anything under visualization/.
#
# Both SurrealDB suites skip themselves under a bare `pytest` (the ws:// suite
# unless EPIMEMER_SURREAL_WS_URL is set; the durability suite unless
# EPIMEMER_SURREAL_PERSIST_TEST is set), so neither runs — nor signals — by
# default. That target is how you actually exercise them.

SURREAL_IMAGE ?= surrealdb/surrealdb:latest
SURREAL_CONTAINER ?= epimemer-surreal-it
# Override when something already holds the port: `make test-integration
# SURREAL_PORT=8123`. The URL follows the port unless you set it too.
SURREAL_PORT ?= 8000
SURREAL_WS_URL ?= ws://127.0.0.1:$(SURREAL_PORT)/rpc
FRONTEND_DIR ?= epimemer/visualization/frontend
BENCH_N ?= 100,1000

.PHONY: test test-frontend test-integration bench

test:
	uv run python -m pytest tests/ -q

# Type-check as well as run: tsc covers the DOM/Cytoscape modules that the unit
# tests deliberately leave alone, so together they cover the whole frontend.
test-frontend:
	cd $(FRONTEND_DIR) && npm run typecheck && npm test

# Run on demand, never in CI — these are measurements, not assertions. Set
# EPIMEMER_BENCH_URL to add a real SurrealDB backend alongside mem://.
# BENCH_N overrides the sizes, e.g. `make bench BENCH_N=100,1000,10000`.
bench:
	uv run python scripts/bench.py --n $(BENCH_N)

# The port check is not paranoia. A process that *accepts* connections on the
# port and then never replies — a stale colima/lima ssh forward is the usual
# culprit — makes docker's publish silently useless, and a `curl` with no
# timeout blocks forever on it rather than failing. The target then hangs
# before running a single test, which reads as a broken test suite.
test-integration:
	@if lsof -nP -iTCP:$(SURREAL_PORT) -sTCP:LISTEN >/dev/null 2>&1; then \
		echo "port $(SURREAL_PORT) is already in use:"; \
		lsof -nP -iTCP:$(SURREAL_PORT) -sTCP:LISTEN; \
		echo "re-run with a free one, e.g. make test-integration SURREAL_PORT=8123"; \
		exit 1; \
	fi
	docker run -d --rm --name $(SURREAL_CONTAINER) -p $(SURREAL_PORT):8000 \
		$(SURREAL_IMAGE) start --user root --pass root memory
	@trap 'docker stop $(SURREAL_CONTAINER) >/dev/null 2>&1' EXIT; \
		echo "waiting for SurrealDB to accept connections..."; \
		ready=0; \
		for i in $$(seq 1 30); do \
			if curl -sf --max-time 2 http://127.0.0.1:$(SURREAL_PORT)/health >/dev/null 2>&1; then ready=1; break; fi; \
			sleep 1; \
		done; \
		if [ "$$ready" != "1" ]; then echo "SurrealDB did not become ready on port $(SURREAL_PORT)"; exit 1; fi; \
		EPIMEMER_SURREAL_WS_URL=$(SURREAL_WS_URL) \
			uv run python -m pytest tests/storage/test_surrealdb_integration.py -v && \
		EPIMEMER_SURREAL_PERSIST_TEST=1 \
			uv run python -m pytest tests/storage/test_surrealdb_persistence.py -v
