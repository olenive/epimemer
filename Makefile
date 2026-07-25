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
SURREAL_WS_URL ?= ws://localhost:8000/rpc
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

test-integration:
	docker run -d --rm --name $(SURREAL_CONTAINER) -p 8000:8000 \
		$(SURREAL_IMAGE) start --user root --pass root memory
	@trap 'docker stop $(SURREAL_CONTAINER) >/dev/null 2>&1' EXIT; \
		echo "waiting for SurrealDB to accept connections..."; \
		ready=0; \
		for i in $$(seq 1 30); do \
			if curl -sf http://localhost:8000/health >/dev/null 2>&1; then ready=1; break; fi; \
			sleep 1; \
		done; \
		if [ "$$ready" != "1" ]; then echo "SurrealDB did not become ready"; exit 1; fi; \
		EPIMEMER_SURREAL_WS_URL=$(SURREAL_WS_URL) \
			uv run python -m pytest tests/storage/test_surrealdb_integration.py -v && \
		EPIMEMER_SURREAL_PERSIST_TEST=1 \
			uv run python -m pytest tests/storage/test_surrealdb_persistence.py -v
