# Epimemer test targets.
#
#   make test              default suite — embedded, no external services
#   make test-frontend     visualization frontend: vitest + tsc type-check
#   make build-frontend    write the visualization bundle (never committed)
#   make bench             scaling measurements (see dev-docs/BENCHMARKS.md)
#   make test-integration  opt-in SurrealDB suites mem:// cannot cover: a real
#                          ws:// connection with cross-connection concurrency,
#                          and on-disk durability across a restart. Needs
#                          Docker; spins up a throwaway server and always tears
#                          it down. Stops if the port is taken.
#
# `make test` stays Python-only: Node is a frontend prerequisite, not a backend
# one, and a missing npm would make the Python suite look broken. Run both
# before touching visualization/.
#
# Both SurrealDB suites skip under a bare `pytest` — the ws:// one unless
# EPIMEMER_SURREAL_WS_URL is set, the durability one unless
# EPIMEMER_SURREAL_PERSIST_TEST is set — so they signal nothing by default.
# `make test-integration` is how they run.

SURREAL_IMAGE ?= surrealdb/surrealdb:latest
SURREAL_CONTAINER ?= epimemer-surreal-it
# Override when something already holds the port: `make test-integration
# SURREAL_PORT=8123`. SURREAL_WS_URL follows it unless set too.
SURREAL_PORT ?= 8000
SURREAL_WS_URL ?= ws://127.0.0.1:$(SURREAL_PORT)/rpc
FRONTEND_DIR ?= epimemer/visualization/frontend
BENCH_N ?= 100,1000

.PHONY: test test-frontend build-frontend test-integration bench

test:
	uv run python -m pytest tests/ -q

# tsc covers the DOM/Cytoscape modules the unit tests leave alone, so the two
# together cover the whole frontend.
test-frontend:
	cd $(FRONTEND_DIR) && npm run typecheck && npm test

# Writes epimemer/visualization/static. The hub serves its API without the
# bundle and says so at `/`; this is what puts the page there.
build-frontend:
	cd $(FRONTEND_DIR) && npm ci && npm run build

# Measurements, not assertions: run on demand, never in CI. Set
# EPIMEMER_BENCH_URL to add a real SurrealDB backend alongside mem://.
# BENCH_N overrides the sizes, e.g. `make bench BENCH_N=100,1000,10000`.
bench:
	uv run python scripts/bench.py --n $(BENCH_N)

# The port check earns its place: a process that accepts connections and never
# replies — usually a stale colima/lima ssh forward — makes docker's publish
# silently useless, and an untimed curl blocks on it forever. The target then
# hangs before one test runs, which reads as a broken suite.
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
