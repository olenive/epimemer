import marimo

__generated_with = "0.21.1"
app = marimo.App(width="medium")


@app.cell
def _(mo):
    mo.md("""
    # Reflection Pipeline

    Step through the reflection Petri net:

    1. **Split** — decompose broad topics into subtopics (placeholder)
    2. **Consolidate** — group similar topics under synthesized parents
    3. **Parallel phase** — decay, enrichment, contradiction detection

    **Note:** This notebook uses a mock embedding provider (SHA256-based vectors) and a mock
    LLM provider, so similarity scores and synthesized descriptions are not semantically meaningful.
    The similarity matrix below shows the actual pairwise scores the system works with.
    """)
    return


@app.cell
def _():
    import copy
    import math
    import marimo as mo
    from petritype.core.executable_graph_components import ExecutableGraphOperations
    from petritype.core.rustworkx_graph import RustworkxGraph
    from petritype.plotting.simple_graphviz import SimpleGraphvizVisualization
    from epimemer.core.types import EmbeddingRecord, Fact, Topic
    from epimemer.embeddings.mock import MockEmbeddingProvider
    from epimemer.llm.mock import MockDecompositionProvider
    from epimemer.pipelines.reflection.reflection_net import ReflectionRequest, reflection_net
    from epimemer.storage.memory import InMemoryStorage

    return (
        EmbeddingRecord,
        ExecutableGraphOperations,
        Fact,
        InMemoryStorage,
        MockDecompositionProvider,
        MockEmbeddingProvider,
        ReflectionRequest,
        RustworkxGraph,
        SimpleGraphvizVisualization,
        Topic,
        copy,
        math,
        mo,
        reflection_net,
    )


@app.cell
async def _(EmbeddingRecord, Fact, InMemoryStorage, MockDecompositionProvider, MockEmbeddingProvider, Topic):
    storage = InMemoryStorage()
    emb_provider = MockEmbeddingProvider(model_id="mock", dimension=8)
    decomp_provider = MockDecompositionProvider()

    # A mix of topics — some should be "similar" (related wording), some distinct.
    # With mock embeddings the similarity is hash-based, not semantic,
    # but we include a range so the threshold slider has something to work with.
    topics = [
        Topic(content="Machine learning techniques", source_id="s1"),
        Topic(content="Machine learning methods", source_id="s2"),
        Topic(content="Deep learning techniques", source_id="s3"),
        Topic(content="Deep learning methods", source_id="s4"),
        Topic(content="Neural network architectures", source_id="s5"),
        Topic(content="Climate change impacts on agriculture", source_id="s6"),
        Topic(content="Renaissance art in Italy", source_id="s7"),
        Topic(content="Quantum computing algorithms", source_id="s8"),
    ]

    # Also add some facts for contradiction detection
    facts = [
        Fact(content="Neural networks require large training datasets", source_id="s1"),
        Fact(content="Neural networks can learn from small datasets", source_id="s2"),
        Fact(content="Python is the most popular ML language", source_id="s3"),
    ]

    _all_nodes = [*topics, *facts]
    for _n in _all_nodes:
        await storage.store_node(_n)
        _vecs = await emb_provider.embed([_n.content])
        await storage.store_embedding(
            EmbeddingRecord(item_id=_n.id, model_id="mock", vector=_vecs[0])
        )

    return decomp_provider, emb_provider, facts, storage, topics


@app.cell
async def _(emb_provider, math, mo, storage, topics):
    # Show the pairwise similarity matrix so the user can see what the system sees
    def _cosine(a, b):
        _dot = sum(x * y for x, y in zip(a, b))
        _na = math.sqrt(sum(x * x for x in a))
        _nb = math.sqrt(sum(x * x for x in b))
        return _dot / (_na * _nb) if _na and _nb else 0.0

    _labels = [t.content[:30] for t in topics]
    _vecs = []
    for _t in topics:
        _embs = await storage.get_embeddings_for_item(_t.id, emb_provider.model_id)
        _vecs.append(_embs[0].vector)

    _pairs = []
    for _i in range(len(topics)):
        for _j in range(_i + 1, len(topics)):
            _sim = _cosine(_vecs[_i], _vecs[_j])
            _pairs.append((_labels[_i], _labels[_j], _sim))

    # Sort by similarity descending
    _pairs.sort(key=lambda x: x[2], reverse=True)

    _lines = ["### Topic Similarity Matrix (mock embeddings)", ""]
    _lines.append("| Topic A | Topic B | Similarity |")
    _lines.append("|---------|---------|------------|")
    for _a, _b, _s in _pairs:
        _marker = " **above 0.85**" if _s >= 0.85 else ""
        _lines.append(f"| {_a} | {_b} | {_s:.3f}{_marker} |")
    similarity_table = "\n".join(_lines)

    _output = mo.md(similarity_table)
    _output
    return (similarity_table,)


@app.cell
def _(mo):
    threshold = mo.ui.slider(
        0.5, 1.0, value=0.85, step=0.01,
        label="Similarity threshold for topic consolidation",
        full_width=True,
    )
    threshold
    return (threshold,)


@app.cell
def _(mo):
    step = mo.ui.slider(
        0, 6, value=0,
        label="Execution step (6 transitions: split → consolidate → broadcast → 3 parallel ops)",
        full_width=True,
    )
    step
    return (step,)


@app.cell
async def _(
    ExecutableGraphOperations,
    ReflectionRequest,
    RustworkxGraph,
    SimpleGraphvizVisualization,
    copy,
    decomp_provider,
    emb_provider,
    mo,
    reflection_net,
    step,
    storage,
    threshold,
):
    # Deep copy so each slider change starts from the original state
    _storage_snapshot = copy.deepcopy(storage)
    request = ReflectionRequest(
        similarity_threshold=threshold.value,
        contradiction_threshold=threshold.value,
        auto_merge=True,
        hierarchical=True,
        model_id="mock",
    )
    graph = reflection_net(request, _storage_snapshot, emb_provider, decomp_provider)

    if step.value > 0:
        graph, fired = await ExecutableGraphOperations.execute_graph(graph, max_transitions=step.value)
    else:
        fired = 0

    pydigraph = RustworkxGraph.from_executable_graph(graph)
    diagram = SimpleGraphvizVisualization.graph(pydigraph)

    _output = mo.md(f"**Threshold:** {threshold.value:.2f} | **Transitions fired:** {fired} / 6")
    _output
    return diagram, graph


@app.cell
def _(diagram, mo):
    mo.image(diagram)
    return


@app.cell
def _(graph, mo, threshold):
    _lines = []
    _any_results = False

    # --- Split ---
    _sp = graph.place_named("SplitResult")
    if _sp and _sp.tokens:
        _any_results = True
        _sr = _sp.tokens[0]
        _lines.append("### Topic Splitting")
        if _sr.topics_split == 0:
            _lines.append("No topics were split (split logic is a placeholder for now).")
        else:
            _lines.append(f"Split **{_sr.topics_split}** topic(s) into subtopics.")
            for _st in _sr.subtopics_created:
                _lines.append(f"- Subtopic: *{_st.content[:80]}*")
        _lines.append("")

    # --- Consolidation ---
    _cp = graph.place_named("ConsolidationResult")
    if _cp and _cp.tokens:
        _any_results = True
        _cr = _cp.tokens[0]
        _lines.append("### Topic Consolidation")
        _lines.append(f"*Using similarity threshold: {threshold.value:.2f}*\n")
        if _cr.pairs_found == 0 and not _cr.parent_topics_created:
            _lines.append("No similar topic pairs found above the threshold.")
            _lines.append("\n*Try lowering the threshold slider to see more pairs matched.*")
        else:
            if _cr.parent_topics_created:
                _lines.append(f"Created **{len(_cr.parent_topics_created)}** parent topic(s) (hierarchical mode):")
                for _pt in _cr.parent_topics_created:
                    _lines.append(f"- Parent: *{_pt.content[:100]}*")
            if _cr.topics_merged > 0:
                _lines.append(f"**{_cr.topics_merged}** topic(s) were flat-merged:")
                for _mt in _cr.merged_topics:
                    _lines.append(f"- Merged into: *{_mt.content[:80]}*")
            _lines.append("\n*Try raising the threshold to group fewer pairs, or lowering it to group more.*")
        _lines.append("")

    # --- Decay ---
    _dp = graph.place_named("DecayResult")
    if _dp and _dp.tokens:
        _any_results = True
        _dr = _dp.tokens[0]
        _lines.append("### Value Decay")
        if _dr.nodes_decayed == 0:
            _lines.append("No active nodes found to apply relevance decay to.")
        else:
            _lines.append(f"Applied relevance decay to **{_dr.nodes_decayed}** active node(s).")
        _lines.append("")

    # --- Enrichment ---
    _ep = graph.place_named("EnrichmentResult")
    if _ep and _ep.tokens:
        _any_results = True
        _er = _ep.tokens[0]
        _lines.append("### Topic Enrichment")
        if _er.topics_enriched == 0:
            _lines.append("No topics needed enrichment (descriptions are sufficiently detailed for their material).")
        else:
            _lines.append(f"Enriched **{_er.topics_enriched}** topic(s):")
            for _et in _er.enriched_topics:
                _lines.append(f"- *{_et.content[:100]}*")
        _lines.append("")

    # --- Contradiction ---
    _xp = graph.place_named("ContradictionResult")
    if _xp and _xp.tokens:
        _any_results = True
        _xr = _xp.tokens[0]
        _lines.append("### Contradiction Detection")
        _lines.append(f"*Using contradiction threshold: {threshold.value:.2f}*\n")
        if _xr.pairs_found == 0:
            _lines.append("No contradictions detected among active facts at this threshold.")
            _lines.append("\n*Try lowering the threshold to detect more potential contradictions.*")
        else:
            _lines.append(f"Detected **{_xr.pairs_found}** potential contradiction(s) between facts:")
            for _pair in _xr.contradiction_pairs:
                _lines.append(f"- Fact `{_pair[0][:8]}...` vs Fact `{_pair[1][:8]}...` (similarity: {_pair[2]:.3f})")
        _lines.append("")

    if _any_results:
        _output = mo.md("\n".join(_lines))
    else:
        _output = mo.md("*No results yet — advance the execution step slider to see results.*")
    _output
    return


if __name__ == "__main__":
    app.run()
