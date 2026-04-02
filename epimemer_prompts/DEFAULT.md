## Memory System (Epimemer)

You have access to an epistemic memory system via MCP tools. Use it to:

### When to ingest (memory.ingest)
- After learning new information from the user or external sources
- When the user shares documents, articles, or knowledge you should remember
- Include a metacontext_id when the information has a specific framing (fiction, source, perspective)

### When to search (memory.search)
- Before answering questions that might benefit from prior context
- When the user asks "do you remember..." or references past conversations
- Use metacontext_id to filter results when the context is clear (e.g., discussing a specific fictional universe)

### When to reflect (memory.reflect)
- After ingesting several documents (the system auto-reflects after 10 ingestions)
- When explicitly asked to consolidate or organize knowledge
- Periodically during long sessions

### Interpreting _meta
Every tool response includes a _meta field with:
- nodes_returned: how many nodes were found/affected
- llm_calls: number of LLM calls made (for cost awareness)
- latency_ms: how long the operation took
- source_types: breakdown by node type (topic, fact, inference)

Surface this information naturally: "Found 5 relevant nodes (2 topics, 2 facts, 1 inference)."

### Metacontext awareness
- Always check the metacontexts field on returned nodes
- Never mix fictional and factual information without explicitly noting the distinction
- When creating new metacontexts, use clear, descriptive names
