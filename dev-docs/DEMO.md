# The demo: `epimemer demo`

A walkthrough that starts from an empty graph, loads a small corpus through
the same tool functions an agent calls, and shows the dashboard doing its job
while it happens. It is the introduction for a new user and the thing to put
on a screen in a presentation. Decided on 2026-10-08, after the marimo
notebooks were removed: the dashboard already shows every net live, so the
one walkthrough worth keeping is one that drives the whole system, not one
net at a time.

Three decisions were taken with the user before this note was written:

| Question | Decision |
|---|---|
| Who decomposes the documents | A fixture shipped with the package, so the demo runs the same every time with no model in the loop. A companion prompt lets a presenter run the same story through Claude Code for the live version. |
| Which embedder | Sentence-transformers when that extra is installed, so similarity means something. Otherwise the mock embedder, with a line on screen saying so. |
| Where it lives | A CLI subcommand, `epimemer demo`, with the corpus inside the package, so it works from an installed tool and shows up in `epimemer --help`. |

## 1. What the demo is for

Someone who has installed Epimemer and opened the README should be able to
run one command and, within a minute, see a graph being built, searched,
corrected and reflected over, with the dashboard open beside the terminal.
The terminal narrates; the dashboard shows. Every stage is a thing Epimemer
does for real: nothing is mocked except, when the extra is absent, the
embedder.

What it is not: a benchmark, a test of the agent, or a tutorial on writing a
decomposition. The companion prompt covers the last of those.

## 2. The story

The corpus is the history of computing from Babbage to the first website,
chosen on 2026-10-08: optimistic, far enough back that nothing in it is
contested in a way that would distract, with firm dates and primary sources
that are public. Nothing after 1991 appears. It is real history, so it is
stored in `the-real`.

Four documents of about two hundred words each, written by us as plain
summaries rather than copied from anywhere:

1. **Engines and the first program, 1822 to 1843.** Babbage proposes the
   Difference Engine, designs the Analytical Engine, completes neither.
   Lovelace translates Menabrea's memoir and adds her Notes, whose Note G
   works through computing Bernoulli numbers on the Engine.
2. **The universal machine and the first machines, 1936 to 1946.** Turing's
   paper on computable numbers, Zuse's Z3, Colossus at Bletchley, ENIAC, von
   Neumann's draft report and the stored program. This document states, as
   sources of its era did, that ENIAC was the first electronic digital
   computer.
3. **Information gets a unit, 1948 to 1950.** Shannon's paper and the bit,
   the 1949 book with Weaver, Hamming's error-correcting codes, Turing's
   imitation game.
4. **From the transistor to the web, 1947 to 1991.** The transistor, the
   integrated circuit, ARPANET's first link, the switch to TCP/IP,
   Berners-Lee's proposal and the first website. It also records that Colossus
   was declassified in 1975 and ran before ENIAC.

What the text carries, and where:

- **Dated events**, well over six across 170 years, so the content-time axis
  has a shape and a collapsed gap in the middle.
- **A world-change**: Shannon's paper is "A Mathematical Theory of
  Communication" in 1948 and "The Mathematical Theory" as the 1949 book.
- **A correction**: document 2's claim that ENIAC was first is retired as
  wrong when document 4 arrives, because Colossus ran in 1944 and was secret
  until 1975. The claim was honestly stated and honestly wrong, which is the
  distinction the demo most wants to show.
- **Corroboration**: two documents independently state that Lovelace's Notes
  contain a program for Bernoulli numbers, and two date the stored-program
  idea to 1945.
- **A held contradiction**: which machine was the first programmable
  computer, Z3 or Colossus or ENIAC, is a genuine disagreement of definitions
  among reputable sources. The demo holds it rather than resolving it.

Sources, listed on the user-facing page and checked when the text is written
rather than recalled: Lovelace's Notes (1843), Turing's 1936 and 1950 papers,
Shannon's 1948 paper and the 1949 book, Hamming's 1950 paper, the 1973
Honeywell v. Sperry Rand decision on the ENIAC patent, Berners-Lee's 1989
proposal, Randell's *The Origins of Digital Computers* and Copeland's
*Colossus*.

Six scenes, each with a one-line narration and a pause (Enter to continue,
`--no-pause` to run straight through):

1. **Setup.** A throwaway graph on SQLite in a temporary directory, the hub
   started or found, a judge named `demo` confirmed in the graph (the person
   running the demo is the user, so confirming is theirs to do). Prints the
   dashboard URL and the session to pick, and waits for the user to open it.
2. **Ingest.** Three documents, each `segment` then `store_decomposition` from
   the fixture. The strip lights the segmentation tile and the edge-creation
   tile per document; the graph panel fills; the activity log gains one entry
   per transaction.
3. **Search.** One query with a declared term, so the lexical arm has work
   and the retrieval tile shows the fork. The response is summarised in the
   terminal; on the dashboard, retrieval focus dims everything the search did
   not return.
4. **The fourth document.** Ingested the same way, it carries the retitled
   book and the declassified Colossus. The demo then retires the 1948 title as
   a world-change and the ENIAC-first claim as wrong, with the two different
   lineage edges, and the timeline panel shows the dated facts in content
   time.
5. **Reflect.** The reflect tile runs its phases. The terminal lists what came
   back: a similar pair, the contradiction, enrichment nominations. The demo
   records one verdict, the pair as one claim, so corroboration has an edge to
   count, and leaves the contradiction held, saying why.
6. **Wrap.** Graph statistics, the path of the graph file, and what to do next:
   `--keep` leaves the graph in place and prints the two environment variables
   that point a server at it, so the next step is opening the same graph from
   Claude Code and asking it questions.

## 3. Shape of the code

`epimemer/demo/`: a package, not a script.

- `corpus/`: the documents as markdown files and the decompositions as one
  JSON file of `store_decomposition` payloads keyed by document name, shipped
  as package data. JSON rather than Python values because it is exactly what
  an agent sends, so the same file is the worked example the companion prompt
  points at.
- `scenes.py`: one function per scene, each taking the demo's dependencies as
  a value and returning what it wants narrated. No module state; the demo's
  dependencies travel the way `ServerConfig` does.
- `run.py`: builds the dependencies, runs the scenes in order, handles pauses
  and the keep-or-delete decision at the end.
- `cli.py` gains the `demo` subcommand and dispatches to `run.py`. The
  command's options: `--keep`, `--no-pause`, `--no-viz`, `--embedder
  auto|mock|sentence-transformers` (default `auto`).

The demo calls the tool functions in `epimemer/mcp/tools.py` directly, the way
`scripts/bench.py` does, so what it exercises is what an agent exercises. It
does not start an MCP server and it does not speak the protocol.

**The visualisation attachment moves out of the server lifespan.** The
lifespan builds the event bus, wraps storage in the instrumented store, probes
or spawns the hub, and starts the hub client with a retrieval record log. The
demo needs every piece of that, so the lifespan's block becomes one function
in `epimemer/visualization/attach.py` that both call, taking the config and
the raw storage and returning the bus, the instrumented storage, the session
info and a stop function. The server keeps its behaviour; the demo stops
duplicating forty lines that have to stay in step.

**The embedder choice** is one function: the extra's module is importable, so
the sentence-transformers provider; otherwise the mock, and the first scene
says which. `--embedder` overrides it, and asking for the real one without the
extra refuses and names the extra, the same way the server does.

**The graph lives in `tempfile.mkdtemp()`** on the SQLite backend, which needs
no server and leaves a file the user can keep. On exit the directory is
removed unless `--keep`. The hub is left running: it is shared by every
session on the machine and stops with `epimemer-viz --stop`.

## 4. The companion prompt

`docs/DEMO.md` is the user-facing page: how to run the demo, what each scene
shows, and the agent-driven variant. That variant is a prompt to paste into
Claude Code with the server pointed at the kept graph, or at a fresh one:
read the four documents from the package, decompose them, store them,
search, correct, reflect, and record verdicts. The fixture JSON is the worked
example the prompt names, so a reader can compare what the agent extracted
with what a careful person did.

## 5. Tests

- The whole demo runs headless in the unit suite, `--no-viz --no-pause` with
  the mock embedder on the in-memory backend, and the test asserts the final
  graph: node counts per type, one node retired as past and one as wrong,
  one similarity edge, one held contradiction, the dated timepoints. This
  pins the fixture to the tool signatures, so a tool change that breaks the
  demo fails here rather than in front of an audience.
- The fixture is validated on its own: every `store_decomposition` payload
  parses, every segment id it names exists in the corresponding document's
  cut, and every tag it uses is described.
- The attachment function is covered by the existing visualisation tests once
  the server calls it; one test runs the demo with the bus on and no hub and
  checks the pipeline events arrive in scene order.
- The `demo` subcommand is covered in `tests/test_cli.py` the way the other
  subcommands are.

## 6. Settled since the first draft

- **The subject**: the history of computing, 1822 to 1991, as in §2. Decided
  2026-10-08.
- **Pace**: scenes pause for Enter by default, `--no-pause` runs straight
  through. A `--delay` option is not built until a recording needs it.
- **The agent-driven variant's judge**: whatever the user approves in the
  picker, as in any session. The demo's own `demo` judge is for the fixture
  path only.
