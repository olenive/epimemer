/**
 * Filtering for the timeline panel.
 *
 * Every filter is a pure predicate over marks the panel already holds in
 * memory, so narrowing a view never costs a round trip.
 *
 * A mark is described by *facets* — the set of values it has for each
 * filterable field, gathered from the mark itself and from every node linked to
 * it. A timepoint linked to one live and one retired fact therefore has both
 * statuses, and stays visible under "active only": the question is whether
 * anything on this mark still matters, not whether everything does.
 */

/** Fields addressable as `field:value` in the text query. */
export const QUERY_FIELDS = [
  "type",
  "status",
  "mc",
  "source",
  "label",
  "content",
] as const;

export type QueryField = (typeof QUERY_FIELDS)[number];

/** Values a mark carries per field. Absent or empty means "not known". */
export type Facets = Partial<Record<QueryField, string[]>>;

export interface FilterableMark {
  start: number | null;
  end: number | null;
  facets: Facets;
}

export interface TimeRange {
  t0: number;
  t1: number;
}

export interface TimelineFilters {
  /** null means unrestricted; an empty set means nothing passes. */
  nodeTypes: ReadonlySet<string> | null;
  statuses: ReadonlySet<string> | null;
  metacontexts: ReadonlySet<string> | null;
  range: TimeRange | null;
  query: string;
}

export const NO_FILTERS: TimelineFilters = {
  nodeTypes: null,
  statuses: null,
  metacontexts: null,
  range: null,
  query: "",
};

/** One clause of a text query. `field` is null for a bare term. */
export interface QueryTerm {
  field: QueryField | null;
  value: string;
}

const isQueryField = (name: string): name is QueryField =>
  (QUERY_FIELDS as readonly string[]).includes(name);

/**
 * Split on whitespace, keeping double-quoted runs together.
 *
 * An unterminated quote yields the rest of the input as one term rather than
 * being dropped — a user mid-type should see their query narrowing, not the
 * filter silently resetting.
 */
const tokenize = (input: string): string[] => {
  const tokens: string[] = [];
  let current = "";
  let quoted = false;

  for (const char of input) {
    if (char === '"') {
      quoted = !quoted;
    } else if (!quoted && /\s/.test(char)) {
      if (current) tokens.push(current);
      current = "";
    } else {
      current += char;
    }
  }
  if (current) tokens.push(current);
  return tokens;
};

/**
 * Parse a query into terms. Terms are ANDed.
 *
 * `field:value` targets one field; a bare term matches any field. A prefix that
 * is not a known field is kept as literal text, colon and all, so that times
 * ("12:30") and URLs do not silently become searches on a field named `12`.
 */
export const parseQuery = (input: string): QueryTerm[] =>
  tokenize(input)
    .map((token) => {
      const at = token.indexOf(":");
      if (at > 0) {
        const name = token.slice(0, at).toLowerCase();
        const value = token.slice(at + 1);
        if (isQueryField(name) && value) return { field: name, value };
      }
      return { field: null, value: token };
    })
    .filter((term) => term.value !== "");

const valuesFor = (mark: FilterableMark, field: QueryField): string[] =>
  mark.facets[field] ?? [];

const allValues = (mark: FilterableMark): string[] =>
  QUERY_FIELDS.flatMap((field) => valuesFor(mark, field));

const containsFold = (haystack: string, needle: string): boolean =>
  haystack.toLowerCase().includes(needle.toLowerCase());

const matchesTerm = (mark: FilterableMark, term: QueryTerm): boolean => {
  const searched = term.field ? valuesFor(mark, term.field) : allValues(mark);
  return searched.some((value) => containsFold(value, term.value));
};

/** True when every term in the query matches somewhere on the mark. */
export const matchesQuery = (mark: FilterableMark, query: string): boolean =>
  parseQuery(query).every((term) => matchesTerm(mark, term));

/**
 * Facet test with "any linked node passes" semantics.
 *
 * A mark with no values at all for the facet is *not* excluded: an unlinked
 * timepoint knows nothing about node types, and hiding it would make a filter
 * that narrows the view also delete data that the filter cannot speak about.
 */
const matchesFacet = (
  mark: FilterableMark,
  field: QueryField,
  allowed: ReadonlySet<string> | null,
): boolean => {
  if (allowed === null) return true;
  const values = valuesFor(mark, field);
  if (values.length === 0) return true;
  return values.some((value) => allowed.has(value));
};

/**
 * True when the mark overlaps the range.
 *
 * An undated mark passes: it lives in the undated lane, off the metric axis,
 * where a date range has nothing to say about it.
 */
export const matchesRange = (mark: FilterableMark, range: TimeRange | null): boolean => {
  if (range === null || mark.start === null) return true;
  return mark.start <= range.t1 && (mark.end ?? mark.start) >= range.t0;
};

/** All filters, ANDed. */
export const matchesFilters = (
  mark: FilterableMark,
  filters: TimelineFilters,
): boolean =>
  matchesFacet(mark, "type", filters.nodeTypes) &&
  matchesFacet(mark, "status", filters.statuses) &&
  matchesFacet(mark, "mc", filters.metacontexts) &&
  matchesRange(mark, filters.range) &&
  matchesQuery(mark, filters.query);

export const applyFilters = <T extends FilterableMark>(
  marks: readonly T[],
  filters: TimelineFilters,
): T[] => marks.filter((mark) => matchesFilters(mark, filters));

/** Every distinct value present for a field, sorted — for populating selects. */
export const facetValues = (
  marks: readonly FilterableMark[],
  field: QueryField,
): string[] =>
  [...new Set(marks.flatMap((mark) => valuesFor(mark, field)))].sort();
