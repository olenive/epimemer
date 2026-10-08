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
#   make test-postgres     opt-in Postgres backend: the behavioural suite
#                          selected to its Postgres parameter, and the
#                          backend's own tests, against a throwaway
#                          postgres:17-alpine. Needs Docker; always tears it
#                          down. Stops if the port is taken.
#
# `make test` stays Python-only: Node is a frontend prerequisite, not a backend
# one, and a missing npm would make the Python suite look broken. Run both
# before touching visualization/.
#
# Both SurrealDB suites skip under a bare `pytest` — the ws:// one unless
# EPIMEMER_SURREAL_WS_URL is set, the durability one unless
# EPIMEMER_SURREAL_PERSIST_TEST is set — so they signal nothing by default.
# `make test-integration` is how they run. The Postgres parameter skips the same
# way unless EPIMEMER_TEST_POSTGRES_DSN is set, and `make test-postgres` is how
# it runs.

SURREAL_IMAGE ?= surrealdb/surrealdb:latest
SURREAL_CONTAINER ?= epimemer-surreal-it
# Override when something already holds the port: `make test-integration
# SURREAL_PORT=8123`. SURREAL_WS_URL follows it unless set too.
SURREAL_PORT ?= 8000
SURREAL_WS_URL ?= ws://127.0.0.1:$(SURREAL_PORT)/rpc
# The Postgres server `make test-postgres` starts. PG_PORT as SURREAL_PORT:
# `make test-postgres PG_PORT=5434` when something already holds 5433. The
# suite drops every graph in the database it is pointed at, which is why it
# only ever gets one this target made.
PG_IMAGE ?= postgres:17-alpine
PG_CONTAINER ?= epimemer-postgres-it
PG_PORT ?= 5433
PG_PASSWORD ?= epimemer
PG_DSN ?= postgresql://postgres:$(PG_PASSWORD)@127.0.0.1:$(PG_PORT)/postgres
FRONTEND_DIR ?= epimemer/visualization/frontend
BENCH_N ?= 100,1000

.PHONY: test test-frontend build-frontend test-integration test-postgres bench

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

# Readiness is asked over TCP inside the container, not the default socket: the
# image initialises with a temporary server that listens on the socket alone,
# then restarts, and a socket check passes during that first one. `-k postgres`
# selects the Postgres parameter across the whole suite, plus every test file
# named for it. `--extra postgres` brings the driver if this environment lacks
# it, and removes nothing.
test-postgres:
	@if lsof -nP -iTCP:$(PG_PORT) -sTCP:LISTEN >/dev/null 2>&1; then \
		echo "port $(PG_PORT) is already in use:"; \
		lsof -nP -iTCP:$(PG_PORT) -sTCP:LISTEN; \
		echo "re-run with a free one, e.g. make test-postgres PG_PORT=5434"; \
		exit 1; \
	fi
	docker run -d --rm --name $(PG_CONTAINER) -e POSTGRES_PASSWORD=$(PG_PASSWORD) \
		-p $(PG_PORT):5432 $(PG_IMAGE)
	@trap 'docker stop $(PG_CONTAINER) >/dev/null 2>&1' EXIT; \
		echo "waiting for Postgres to accept connections..."; \
		ready=0; \
		for i in $$(seq 1 30); do \
			if docker exec $(PG_CONTAINER) pg_isready -h 127.0.0.1 -U postgres >/dev/null 2>&1; then ready=1; break; fi; \
			sleep 1; \
		done; \
		if [ "$$ready" != "1" ]; then echo "Postgres did not become ready on port $(PG_PORT)"; exit 1; fi; \
		EPIMEMER_TEST_POSTGRES_DSN=$(PG_DSN) \
			uv run --extra postgres python -m pytest tests -q -k postgres
