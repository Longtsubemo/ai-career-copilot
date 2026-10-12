# Phase 1I — Job Search Relevance and Matching Quality

## Summary

Discovery now compares roles and locations far more sensibly, entirely
deterministically and without any new service:

- **Location normalization** — case, accents, punctuation and a small set of
  well-established aliases are folded so `Bengaluru` and `Bangalore` (and
  `Bombay`/`Mumbai`, `Madras`/`Chennai`, `Calcutta`/`Kolkata`) compare equal.
  The stored display value is never rewritten.
- **Role normalization and a conservative taxonomy** — `AI Engineer` and
  `Artificial Intelligence Engineer` match exactly; `ML Engineer` and
  `Machine Learning Engineer` match exactly; the genuinely related families
  `Generative AI`, `LLM` and `Machine Learning` are recognised as *related,
  not identical*. A bare `Engineer` no longer matches every engineering role.
- **Bounded query expansion** — the primary query is the request verbatim; at
  most one well-grounded role-alias query is appended (`AI Engineer` also
  searches `Artificial Intelligence Engineer`). Never dozens of searches,
  never a location or experience rewrite.

No new dependency, no geocoding provider, no scheduler, no background worker,
no schema change. The discovery agent's honest-persistence contract is
untouched.

A read-only follow-up review of the live run surfaced two narrower issues,
fixed in this phase (see *Follow-up* below): a failed re-crawl could overwrite
previously extracted vacancy content with a search-result snippet, and the
`extractions_succeeded` metric counted only usable crawls that survived
filtering. Both are corrected with regression tests and documentation.

## Files changed

### Backend

- `backend/app/agents/discovery/relevance.py` — **new.** Pure, LLM-free rules:
  `normalize_location`, `normalize_role`, `location_matches`, `role_family`,
  `related_families`, `RoleMatch`, plus the data tables
  `LOCATION_ALIAS_GROUPS`, `ROLE_FAMILIES`, `RELATED_ROLE_FAMILIES` and
  `ROLE_ALIASES`.
- `backend/app/agents/discovery/extract.py` — `FilterDecision` gains
  `role_match`; `filter_check` now routes the role through the taxonomy when
  the request names a known family, compares location via
  `location_matches`, and explains every rejection (and the related-family
  classification of an accepted title).
- `backend/app/agents/discovery/query.py` — new `build_search_queries(request,
  *, max_queries=2)` returning the bounded, deduplicated, ordered query list;
  `build_search_query` is unchanged and is always the first entry.
- `backend/app/agents/discovery/agent.py` — `DiscoveryAgent`/`register_discovery_handler`
  gain `max_queries` (default 2, validated `>= 1`); `discover` runs the bounded
  query set in order, keeps every result, continues past an
  additional-query failure (recording it in `errors`) while still raising when
  the primary query fails; the result echoes the executed query list.
- `backend/app/agents/discovery/contract.py` — `DiscoveryResult.queries`
  (`list[str]`, defaults empty) echoing the executed queries.
- `backend/app/agents/discovery/__init__.py` — re-exports the new names.

### Frontend

- `frontend/src/types/api.ts` — `DiscoveryOutput.queries: string[]` mirrored.

### Tests

- `backend/tests/test_discovery_relevance.py` — **new, 42 tests**: location
  normalization/aliases, role normalization, the family taxonomy and its
  relation graph, role filtering (exact/related/reject), and bounded query
  expansion (alias, non-taxonomy role, extra tokens, `max_queries`).
- `backend/tests/fake_clients.py` — `FakeSearXNGClient` accepts `fail_after`
  so a bounded multi-query run can have a working primary query and a failing
  secondary one.
- `backend/tests/test_discovery_agent.py` — role/location filtering tests
  rewritten for the taxonomy; added single-query mode and secondary-query
  failure tests; counts updated for the bounded query set.
- `backend/tests/test_discovery_runtime.py`,
  `backend/tests/test_discovery_quality.py`,
  `backend/tests/api/test_api_discovery.py`,
  `backend/tests/api/test_api_workflow.py` — query-list and count assertions
  updated; diagnostic assertions made robust to cross-query duplicates.

## Location normalization (Part A)

`normalize_location` folds NFKD accents, casefolds, tokenizes on `[a-z0-9]+`
and maps alias tokens to a canonical spelling. It is used only for comparison;
the extracted and persisted `Job.location` keeps the original text.

| Group | Tokens folded together |
| --- | --- |
| Bangalore | `bangalore`, `bengaluru` |
| Mumbai | `mumbai`, `bombay` |
| Chennai | `chennai`, `madras` |
| Kolkata | `kolkata`, `calcutta` |

`location_matches(requested, job_location)` returns true when the two
normalized token sets intersect. A missing/blank job location never conflicts
(absent evidence is never a rejection, exactly as before). Consequence: a
`Bangalore` request now also accepts `Bengaluru Urban, Karnataka`, which the
old token-overlap rule rejected.

## Role normalization and taxonomy (Part B)

`normalize_role` folds case and punctuation (`AI/ML Engineer` ->
`ai ml engineer`). `role_family` classifies a role or title into the smallest
family whose phrase tokens are all present.

| Family | Phrase tokens (any one matches) |
| --- | --- |
| `generative_ai` | `generative ai engineer`, `gen ai engineer` |
| `llm` | `llm engineer`, `large language model(s) engineer` |
| `ml` | `ml engineer`, `machine learning engineer` |
| `ai` | `ai engineer`, `artificial intelligence engineer` |

All four families are mutually *related* (but never identical to themselves);
`related_families` is the single source of that relation.

`filter_check` role rules:

| Request role | Title | Decision | `role_match` |
| --- | --- | --- | --- |
| in the taxonomy | same family (incl. aliases) | accept | `exact` |
| in the taxonomy | a related family | accept, reason names the related family | `related` |
| in the taxonomy | no family (`Engineer`, `Data Scientist`, `Product Manager`) | reject: "carries no '<family>' role signal" | — |
| in the taxonomy | an unrelated family | reject: "unrelated to requested" (guard for a future taxonomy) | — |
| outside the taxonomy | any | original token-overlap behaviour | `None` |

A multi-token family phrase is required, so `AI Platform Engineer` matches
`ai` while a bare `Engineer` does not. Roles outside the taxonomy (for
example `Data Scientist`) keep the previous behaviour unchanged.

## Query construction (Part C)

`build_search_queries` starts from `build_search_query(request)` — the request
verbatim, `"{role} {location} jobs {experience}"` — and appends **one**
`ROLE_ALIASES` query when the normalized role is exactly a known phrase and
the budget allows. The alias is a well-grounded synonym only
(`ai engineer` <-> `Artificial Intelligence Engineer`, `ml engineer` <->
`Machine Learning Engineer`, `llm engineer` <-> `Large Language Model
Engineer`, `gen ai engineer` <-> `Generative AI Engineer`). A role with extra
tokens (`Senior AI Engineer`) or outside the taxonomy does not expand.

Examples:

```
AI Engineer    -> ["AI Engineer Bangalore jobs 1-3 years",
                   "Artificial Intelligence Engineer Bangalore jobs 1-3 years"]
Data Scientist -> ["Data Scientist Bangalore jobs 1-3 years"]
max_queries=1  -> ["AI Engineer Bangalore jobs 1-3 years"]
```

The agent runs the queries in order and merges every result; canonical-URL
dedup already collapses overlap, and `searched` counts all results returned.
Cancelling expansion reduces to the identical single query.

## Verification (Part F)

Backend (`backend/` with the venv active):

- Full suite: `python -m pytest -q` → **896 passed** (893 after the search
  changes, plus 3 regression tests from the follow-up below).
- `python -m alembic upgrade head` then `python -m alembic check` →
  **No new upgrade operations detected** (no schema change was made).

Frontend (`frontend/`):

- `npm test` → **45 passed**.
- `npm run build` (`tsc -b && vite build`) → **success**.

## Live smoke test (Part E)

One targeted run against the live services, using the existing SearXNG and
Firecrawl clients built from application settings, with `max_candidates=5` and
`max_queries=1` (exactly one search): role `AI Engineer`, location
`Bengaluru`, experience `1-3 years`.

| Measure | Value |
| --- | --- |
| Total jobs before | 17 |
| Total jobs after | 17 |
| Executed queries | `["AI Engineer Bengaluru jobs 1-3 years"]` |
| Results searched | 20 |
| Candidates considered (capped) | 5 |
| Scrape attempts | 5 |
| Extractions succeeded | 0 |
| Jobs created | 0 |
| Jobs updated | 1 |
| Jobs skipped by the filter | 4 |
| Listing pages skipped | 0 |
| Duplicates skipped | 0 |
| Scrape failures | 2 |

Two candidate scrapes failed at the provider with HTTP 500 (a LinkedIn and an
Indeed URL); the run continued honestly, recorded both in `errors`, and left
every candidate accounted for
(`created + updated + skipped + listings_skipped + invalid_results ==
candidates_found`; here `0 + 1 + 4 + 0 + 0 == 5`). One pre-existing job was
re-verified/updated in place; no new row was created, so the table size is
unchanged. No record was deleted or reset.

The `Extractions succeeded = 0` value is the number **as reported by the
run at the time**, before the metric was corrected (see the follow-up section
below). Under the corrected semantics — usable crawls counted independent of
filtering — the same historical run would report `extractions_succeeded = 3`,
because `scrape_attempts (5) == extractions_succeeded + scrape_failures (2)`.
This is a derived restatement of the recorded run, not a new live result; no
additional provider request was made.

Relevant caveat: because the candidates that could have carried a location
were either filtered or failed to scrape, the live run did not surface a
non-null location to observe alias matching on; that path is covered
deterministically by `tests/test_discovery_relevance.py`.

## Follow-up: failed-crawl content protection and metric clarity

A read-only consistency review of the smoke run (5 candidates, 5 scrape
attempts, 0 extractions, 1 update) identified two issues.

### 1. A failed crawl could degrade a previously good job

`_upsert_job` (`backend/app/agents/discovery/agent.py`) used
`coalesce(excluded, existing)` for every content column. Because
`extract_job` always yields a non-null `title` (falling back to the search
snippet, `extract.py`) and `job_url` (the raw result URL), a re-crawl that
failed still overwrote an existing job's reliable title, description and
outbound URL with search-derived text — exactly what happened to the
smoke run's single updated row, whose title became the Indeed listing
snippet after its scrape returned HTTP 500.

Fix: the vacancy-content columns (title, description, location, employment
type, experience, company id, outbound URL, source, date posted) are now
reconciled **only when the crawl produced usable content** (`plan.outcome.
scraped`). After a failed or unusable crawl, only the operational columns
(`crawl_status`, `last_seen_at`, `updated_at`) change; availability,
`crawled_at`, `first_seen_at` and all extracted content are preserved. A
first-seen row is still inserted from the search-derived fields, so the
existing "persist a new job from search metadata after a failed crawl"
contract is unchanged, and a successful crawl updates content as before.

### 2. `extractions_succeeded` did not mean what it said

The counter was incremented **after** the role/location filter and the
listing-page classification `continue`s, so it silently counted only usable
crawls that also survived filtering. The counter is now incremented
immediately after a usable crawl (`agent.py`), independent of any later
rejection, which makes the documented identity
`scrape_attempts == extractions_succeeded + scrape_failures` hold exactly.
Every `DiscoveryResult` counter now has a documented meaning in the model
docstring (`contract.py`), and `AGENTS.md` records the corrected invariant
and crawl semantics.

### Regression tests

- `test_failed_recrawl_cannot_degrade_previously_crawled_content` — a failed
  re-crawl (with a different search snippet and a tracking-param URL) leaves
  the prior title, description, location, employment type and outbound URL
  intact, keeps `availability`/`crawled_at`, marks `crawl_status = failed`,
  and advances `last_seen_at`.
- `test_successful_recrawl_refreshes_previously_crawled_content` — a usable
  re-crawl still refreshes title, description and experience level and
  advances `crawled_at`.
- `test_usable_crawl_rejected_by_filter_still_counts_as_extraction` and an
  added assertion in `test_page_listing_reclassification_skips_after_crawl` —
  a usable crawl counts as an extraction even when filtering or the listing
  classifier later rejects it.
- Both `assert_invariant` helpers in `test_discovery_agent.py` /
  `test_discovery_quality.py` now also assert
  `scrape_attempts == extractions_succeeded + scrape_failures`.
- `test_failed_crawl_is_recorded_without_faking_success` now also asserts the
  search-derived description is persisted for a first-seen failed-crawl row.

No migration was required; only application logic, tests and documentation
changed.

## Limitations

- The taxonomy is deliberately narrow: only the AI/ML engineer cluster
  (`ai`, `ml`, `llm`, `generative_ai`). Roles outside it keep the old
  token-overlap rule, so `Data Engineer` still matches a `Data Scientist`
  request on the shared `data` token, exactly as before.
- Related families are accepted, not ranked — discovery has no relevance
  score, so a related match is distinguished in `FilterDecision.role_match`
  and the decision reason rather than demoted in ordering.
- Location aliases are a fixed, well-established set; no geocoding or
  fuzzy matching ("Bengaluru" still does not equal a misspelled
  "Bengaluroo"). The `GET /api/jobs?location=` list filter remains a plain
  case-insensitive substring match; it is not yet normalized.
- Query expansion adds at most one alias query and merges results through the
  existing canonical-URL dedup; `searched` therefore counts results across all
  queries, not unique pages.

## Next milestone (proposed)

- Persist a normalized location/role key alongside the display columns (needs
  a small migration) so `GET /api/jobs` filtering can use the same alias
  semantics without a full scan.
- Widen the taxonomy to more families with an explicit relation graph once
  the AI/ML cluster has proven stable in real discovery runs.
