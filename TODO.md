[x] Make sure we have a way to merge Topic and other kinds of nodes when it turns out that we have approached basically the same topic from different directions.
    DONE for Topics: `merge_nodes` (atomic, embeds + migrates/dedupes edges, retires
    sources as MERGED with `merged_into` lineage) is wired into `apply_reflection`
    (merges=[{source_ids, content}]) behind a high pairwise-similarity bar (default 0.92).
    OPEN: extending the wired merge to Facts/Inferences — under discussion (Inferences
    are designed to let competing derivations coexist, so merge there should be rare).

[x] Also we need a way divide up a Topic or other node if it's description/context gets too big and we need to break it up into sub-topics. Ideally this sub-division would have an inherent hierarch so that when recalling the topic we can drill down into specific sub-topics without having to load everything into context.
    DONE: `apply_reflection splits=[{topic_id, subtopics}]` builds the `subtopic_of`
    hierarchy, and recall now uses it — `search` annotates returned Topics with their
    parents/subtopics (id + preview), and the `topic_tree` tool returns ancestors plus
    descendants to a depth as previews only, so a caller drills into one branch instead
    of loading the subtree.


[~] Let's make sure we have a visible counter for the auto-reflect - also let's discuss how we can allow the user to trigger the reflect step sooner or to delay it.
    PARTIAL: `graph_stats` reports the count, the threshold in force, and whether a
    reflect is due; `configure_reflection` sets a per-graph threshold (reflect sooner
    by lowering it or just calling `reflect`; delay by raising it). Deliberately no
    way to zero the counter without reflecting — that discards the signal rather than
    deferring it. OPEN: the visualization header badge (ISSUES.md #26 scope 3).
