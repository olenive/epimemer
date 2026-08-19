Prefer a functional programming style.
Minimise the use of inheritence.
Use type annotations in Python where possible but don't overcomplicate the code in an attempt to have perfect type hints.
Avoid making classes that use `self` or `@staticmethod`.
However, using Pydantic BaseModel for data structures is encouraged.
Prefer uv over pip.
Use the Petritype library `../petritype` for complex precesses and data pipelines.
Remember to run `uv lock --upgrade-package petritype` in case Petritype main branch has been updated.
When using Marimo notebooks remember to not re-define variables in different cells, that cells correspond to functions and these funcitons need to return values.

Our goal is to build a robust and secure system, not simply a prototype. We don't want to trade speed for technical debt.

# Git Usage
- Do not merge into the main branch without asking.
- Keep commit messages very succinct.
- Do not add "Co-Authored-By" or similar to commit messages!

# Frontend Coding Style
1. Prefer a functional programming style.
2. Prefer Typescript over plain Javascript.
3. Use Tailwind CSS.

# Memory System (Epimemer)
You have access to an epistemic memory system via MCP tools. Use it to:

### When to ingest (segment + store_decomposition)
- After learning new information from the user or external sources
- When the user shares documents, articles, or knowledge you should remember
- Include a metacontext_id when the information has a specific framing (fiction, source, perspective)

### Value priors at ingest (importance, confidence)
Each topic/fact/inference may be an object rather than a bare string, carrying priors only you can supply — you have read the material and nothing downstream will read it again.
- **`importance`** (0.0–1.0, default 0.5) — set it only for the unusually consequential or unusually disposable. Triviality is properly judged at reflect time, once the neighbourhood exists.
- **`confidence`** (0.0–1.0) — how well the record would back this claim up if it were challenged. A property of the evidence, **not** of how far you agree with it, and not of how much it matters. **Omit it by default**; omitting stores "unrated", which is deliberately different from a rated 0.5.
  - `0.3` — the source hedges, is partisan on this point, or the claim is your reading of the text rather than what it states
  - `0.5` — stated plainly, no specific reason to doubt or specially trust it → **omit the field**
  - `0.7` — stated as established, by a source in a position to know
  - `0.9` — a primary or authoritative source *for this claim*: the person about their own preference, the spec about its own behaviour
  - Rate **per node, never per document** — the same message can carry a 0.9 preference and a 0.3 guess, which matters most for conversation with the user, the commonest ingest here.
  - Inside a metacontext the **frame is the record**: a fictional fact can honestly be 0.9. Confidence is not a fiction detector; metacontexts already carry that.
  - Never lower it for contradiction or for age — `record_contradiction` and `created_at` carry those and stay current, while a prior freezes.
- **`confidence_basis`** — one line saying why, whenever you supply a confidence other than 0.5. Not required, but a high prior nobody can review later is worth little.

### When to search (search)
- Before answering questions that might benefit from prior context
- When the user asks "do you remember..." or references past conversations
- Use metacontext_id to filter results when the context is clear (e.g., discussing a specific fictional universe)

### When to reflect (reflect)
- After ingesting several documents (the system auto-suggests reflection once a configured threshold of ingestions is reached — it flags the suggestion, it does not reflect on its own)
- When explicitly asked to consolidate or organize knowledge
- Periodically during long sessions

### Interpreting _meta
Every tool response includes a _meta field with:
- nodes_returned: how many nodes were found/affected
- latency_ms: how long the operation took
- source_types: breakdown by node type (topic, fact, inference)

Surface this information naturally: "Found 5 relevant nodes (2 topics, 2 facts, 1 inference)."

### Multi-graph management (list_graphs, use_graph, delete_graph)
- Use `list_graphs` to see available knowledge graphs and which is active
- Use `use_graph` to switch between graphs or create new ones (requires confirmation)
- All backends support multiple named graphs (default graph is "default")
- With SurrealDB, each graph is a separate database within the namespace

### Metacontext awareness
- Always check the metacontexts field on returned nodes
- Never mix fictional and factual information without explicitly noting the distinction
- When creating new metacontexts, use clear, descriptive names
