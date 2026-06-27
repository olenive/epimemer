[x] Make sure we have a way to merge Topic and other kinds of nodes when it turns out that we have approached basically the same topic from different directions.
    DONE for Topics: `merge_nodes` (atomic, embeds + migrates/dedupes edges, retires
    sources as MERGED with `merged_into` lineage) is wired into `apply_reflection`
    (merges=[{source_ids, content}]) behind a high pairwise-similarity bar (default 0.92).
    OPEN: extending the wired merge to Facts/Inferences — under discussion (Inferences
    are designed to let competing derivations coexist, so merge there should be rare).

[~] Also we need a way divide up a Topic or other node if it's description/context gets too big and we need to break it up into sub-topics. Ideally this sub-division would have an inherent hierarch so that when recalling the topic we can drill down into specific sub-topics without having to load everything into context.
    PARTIAL: `apply_reflection splits=[{topic_id, subtopics}]` creates subtopic nodes
    linked to the parent via `subtopic_of` edges (the hierarchy exists). OPEN: making
    recall actually drill down the hierarchy instead of loading everything (a retrieval
    feature, not yet implemented).


[ ] Let's make sure we have a visible counter for the auto-reflect - also let's discuss how we can allow the user to trigger the reflect step sooner or to delay it.
