# Discovery HTTP 500 Investigation

> **Status update (2026-10-14):** The initial investigation and replay notes
> below are retained as history. Their conclusions about missing historical
> Firecrawl traces, absent provider diagnostics, and the need for a replay were
> superseded by later log recovery and source inspection. Current findings and
> implementation are summarized in the final sections.

## Scope and repository state

Inspected on 2026-10-14. The repository was clean on branch
`fix/firecrawl-discovery-errors`; expected commit
`dc0d41c4444ccd20959a61656a0ed1b5eaac96ff` is an ancestor of `HEAD`.
No branch switch or application request was made.

## Confirmed evidence (initial inspection snapshot)

- The effective Firecrawl base URL resolved from the current settings is
  `http://localhost:3002`; no API key is configured. The Firecrawl API and
  Playwright service containers were running when their status was read.
- [`backend/app/clients/firecrawl.py`](../backend/app/clients/firecrawl.py)
  `FirecrawlClient.scrape` sends `POST {base_url}/v2/scrape` with JSON
  `{ "url": <candidate URL>, "formats": ["markdown"] }`, JSON content type,
  and an optional bearer token. Its default timeout is 30 seconds.
- For a non-2xx response, `scrape` catches `httpx.HTTPStatusError` and raises
  `FirecrawlError` with the received status and `kind="http_error"`. A 500 is
  therefore distinct from a timeout or network error. `success=false` JSON is
  `api_error`; invalid JSON or response shape is `shape_error`.
- **Superseded:** At this initial inspection, the client did not retain
  structured response diagnostics. See the implementation below for the
  bounded fields now retained. Firecrawl still does not return its internal
  scrape/job ID as a response correlation header.
- [`backend/app/agents/discovery/agent.py`](../backend/app/agents/discovery/agent.py)
  `_crawl_failure_category` maps status 500 to `http_error`; `_crawl` includes
  the candidate URL and client message in the run's `errors`, and the bounded
  `issues` entry carries the sanitized URL, stage, category, status, and same
  message. Thus discovery preserves that an HTTP 500 occurred and which
  candidate it affected, but not why the service returned it.
- [`backend/tests/test_firecrawl_client.py`](../backend/tests/test_firecrawl_client.py)
  covers the POST path/body, HTTP 500 mapping, invalid JSON, API failure,
  transport failure, and successful response parsing. These tests support the
  intended request and error mapping; they are mocked and do not validate the
  historical service response.
- [`docs/PHASE_1I_JOB_SEARCH_QUALITY_REPORT.md`](PHASE_1I_JOB_SEARCH_QUALITY_REPORT.md)
  documents five attempts and two HTTP 500s associated with LinkedIn and
  Indeed, but does not include response bodies, request IDs, or a correlated
  service trace.
- A read-only database check found a discovery run dated 2026-10-08 for
  `AI Engineer` / `Bangalore` / `1-3 years` with three error strings ending in
  `scrape returned HTTP 500`. It does not match the report's two-failure
  result, so it cannot be identified as that run. Its stored diagnostics also
  contain no provider response details.
- The current Firecrawl/Playwright container log tails (read on 2026-10-14)
  contain separate target fetches with 403/429 responses and successful
  fetches, including for Indeed/LinkedIn pages. They show those target fetches
  can vary, but do not show the two reported 500s or a matching internal
  exception. The available tail is not a historical trace of the Phase 1I run.

## Diagnosis (initial snapshot; superseded below)

**Confirmed:** the application client observed HTTP 500 responses from its
configured Firecrawl endpoint. This rules out a client-side timeout or network
failure for those attempts. The active endpoint is local Firecrawl at port
3002, but an HTTP status alone cannot prove whether the response was generated
by Firecrawl itself or an intermediary in front of it.

**Likely but unconfirmed:** the failure occurred after the request reached an
HTTP server at that endpoint. A Firecrawl API failure, downstream browser or
worker failure, target response that Firecrawl could not handle, or an
intermediary response remain possible. Current separate 403/429 target
responses do not establish a cause for these 500s. There is no evidence to
label them as bot protection.

**Not demonstrated:** request construction or successful-response parsing is
incorrect. The request shape matches the client tests and the configured
`/v2/scrape` path. The available evidence does not identify a response-shape
problem because the recorded failures were HTTP 500 responses.

**Diagnostic gap:** application diagnostics preserve status, broad category,
sanitized candidate URL, and a generic status message; they discard the
provider's HTTP error body and correlation headers. The historical report and
available logs do not connect either failure to a Firecrawl API stack trace or
browser event.

## Conclusion and next action (historical; superseded below)

No request-construction fix is justified by the evidence. A diagnostic change
is also premature without knowing whether the missing evidence is in the
response body or service logs. The smallest useful next action is one bounded
replay of a single affected URL, capturing its timestamp, Firecrawl response
body/request ID, and matching API plus Playwright logs. That can distinguish a
service exception from a downstream fetch result before any code change is
considered.

No tests were run; inspection and database access were read-only.

## One-request replay attempt (2026-10-14; earlier aborted attempt)

- Preflight remained on `fix/firecrawl-discovery-errors`. The only working-tree
  change was this uncommitted investigation report. Read-only container status
  showed both `retrieval-firecrawl-api-1` and
  `retrieval-firecrawl-playwright-service-1` already running.
- The exact Phase 1I URL could not be recovered reliably. The Phase 1I report
  identifies LinkedIn and Indeed but does not record either full URL. The
  retained 2026-10-08 database run is not a safe substitute: it has input
  location `Bangalore` and three HTTP 500 error entries, whereas the Phase 1I
  report describes `Bengaluru` and two failures. Available container logs are
  later and have no matching historical trace.
- **No Firecrawl request was sent.** Replay timestamps, status, request ID, and
  response diagnostics are therefore not applicable. No URL was substituted.
- Cause remains unresolved. The smallest next step is to recover one exact
  affected URL from the original Phase 1I execution transcript or raw run
  diagnostics; only then is the single-request replay safe to perform.

## Initial safe diagnostic retention update (formatter gap later identified)

`FirecrawlError` now retains the HTTP status, an allowlisted and validated
request/correlation ID, and a constrained provider error code or concise
message when those fields are available in a small structured JSON response.
Provider messages are dropped when oversized or HTML/stack-trace-like; URLs,
query values, bearer credentials, and common secret fields are redacted, and
the retained message is capped at 240 characters. Response bodies are not
stored. At that point, Discovery attached safe values to `LogRecord.extra`; the
default formatter did not serialize those fields, so they were not visible in
normal application logs. The persisted/API-visible failure text remained
generic.

Offline tests in `backend/tests/test_firecrawl_client.py`,
`backend/tests/test_discovery_diagnostics.py`, and
`backend/tests/test_discovery_classify.py` cover HTTP 500 diagnostics,
redaction/bounds, missing IDs, failure-kind mapping, discovery log fields, and
URL sanitization. The historical Phase 1I root cause remained unresolved at
that point.

## Later historical evidence and source findings

- The 2026-10-08 run was later correlated in existing logs: Playwright
  recorded HTTP 403 for the Naukri path, while the Firecrawl API logged engine
  fallback/exhaustion as `SCRAPE_ALL_ENGINES_FAILED` and returned HTTP 500.
  The logs include internal `scrapeId`/`jobId` values and fallback names
  `pdf`/`document`. The 403 is confirmed; why Naukri returned it is not. The
  engine names alone do not establish that the URL was misclassified.
- The corrected 2026-10-14 replay returned HTTP 500, and Playwright again
  recorded HTTP 403. No matching API log entry was found in the inspected
  window. The missing API entry remains unexplained.
- The exact running API image inspected was
  `ghcr.io/firecrawl/firecrawl:latest`, digest
  `sha256:672588cafb8ae2b86c54fdf5e5de186023112e0c98b0078f533f95222a55fa5a`,
  with `/app/BUILD_SHA` `58d5eef59a7842f33f547b3164f47d55ce8855bb`. The
  compiled controller in `/app/dist/src/controllers/v2/scrape.js` returns
  `{success:false, code:e.code, error:e.message}` for a `TransportableError`
  such as `SCRAPE_ALL_ENGINES_FAILED`. It does not return `jobId`/`scrapeId`
  or set a correlation-ID response header on that path.
- The controller accepts `External-Request-Id` and passes it to
  `logRequest`; the inspected helper documents storage on its request record.
  The source does not establish propagation of that header into worker or
  engine logs. The application must not treat its own ID as a provider-issued
  request ID.

## Implemented observability update

- `FirecrawlClient.scrape` generates a UUID correlation ID for each outgoing
  scrape, sends it in `External-Request-Id`, and retains the same ID on
  `FirecrawlError`. A provider request ID remains a separate field and is
  retained only if Firecrawl actually returns an allowlisted header.
- HTTP failures retain status, error kind, a validated top-level provider
  `code`, and a short sanitized provider message when the JSON body is within
  the existing 16 KiB parsing limit. Raw bodies are not retained; malformed,
  absent, non-JSON, or oversized diagnostics do not replace the original HTTP
  failure. The controller-shaped regression test exposed and fixed a parser
  bug where the long uppercase code `SCRAPE_ALL_ENGINES_FAILED` was mistaken
  for an opaque token. Opaque undelimited token-like codes remain rejected.
- Discovery emits an explicit JSON allowlist in the log message: category,
  error kind, status, application correlation ID, provider request ID if one
  was returned, provider error code, and sanitized provider message. The
  configured formatter prints the message. The correlation ID and provider
  code are not added to `DiscoveryIssue` or `AgentRunOut.output`, which is
  public; user-facing failure text stays generic.
- No database/model/API schema change was needed, so no migration was added.
- Focused offline coverage is in
  `backend/tests/test_firecrawl_client.py` and
  `backend/tests/test_discovery_diagnostics.py`; existing URL sanitization
  coverage remains in `backend/tests/test_discovery_classify.py`. No live
  request was made for this implementation.
- Verification from `backend/`: those three focused modules passed (69 tests),
  and `git diff --check` passed.

The reason for Naukri's HTTP 403 and the absent October 14 API log entry remain
unknown. The application correlation header can be used to look up the
Firecrawl request record when it is retained, but the inspected source does
not show that ID being propagated to worker or engine logs, and Firecrawl does
not echo its internal job ID in the scrape error response.

## Current sanitization policy

- Public `DiscoveryIssue.url` contains only a validated HTTP(S) origin. Paths,
  queries, fragments, and embedded credentials are excluded; malformed or
  unsupported URLs become `<invalid-url>`.
- The recognized provider error-code allowlist contains only
  `SCRAPE_ALL_ENGINES_FAILED`. Unknown codes are rejected. Provider messages
  are also checked for uppercase underscore-separated code-like identifiers;
  if one is not on that same allowlist, the entire message is dropped so the
  code cannot bypass the code-field policy through diagnostic logs.
- Absolute URLs are removed from otherwise safe messages. Messages containing
  remaining path or fragment syntax are rejected before discovery logging,
  including slash-prefixed and relative paths, backslash paths, and common
  percent-encoded, HTML-entity, or Unicode compatibility forms. This favors
  dropping ambiguous provider text over retaining possible path data.
- Retained diagnostic fields remain bounded and internal. The application
  correlation ID and separately validated provider request ID, HTTP status,
  error kind, and any accepted provider code remain available if a message is
  rejected. Public discovery failures continue to use generic messages and do
  not expose these diagnostic fields.
- These changes improve observability and privacy. They do not explain why
  Naukri returned HTTP 403; that target-side cause remains unknown.

## Endpoint-level regression coverage note

The existing offline tests cover `DiscoveryResult` serialization and emitted
discovery logs. The `/api/jobs/discover` endpoint-level failure-response
boundary has not been directly regression-tested. Database-backed API tests
were excluded from the previous audit because their fixtures can create and
drop a PostgreSQL test database.

Database-backed persistence tests were not rerun after the latest HTTP 200
page-validation change. This is separate from the endpoint-level coverage gap
above; the page-validation regression checks in this change use offline fake
clients and a captured persistence plan.

## Page-evidence validation hardening

The earlier synthetic HTTP 200 false acceptance occurred because a
job-specific-looking title plus a long description met the minimum content
checks, without requiring positive vacancy evidence or recognizing common
page-level failure text. Validation now checks challenge and clear error
signals in page titles, headings, and opening content independently of the
reported HTTP status. Pages without `JobPosting` structured data must also
show job-specific evidence, such as recognized vacancy section headings or
multiple distinct job-action terms. Structured postings remain usable when
their title and description are sufficient and the page surface does not
contradict them with a clear failure signal. Failed validations retain generic
public issues and cannot enter the persistence plan; existing failed-recrawl
updates remain limited to operational fields.

Focused offline regression tests cover the original HTTP 200 internal-error
reproduction, generic failure and challenge pages, positive vacancy evidence,
structured-data conflict handling, and safe persistence planning. This
hardening prevents false acceptance of recognized HTTP 200 failure templates;
it does not explain why Naukri returned HTTP 403. The historical sequence
remains Naukri HTTP 403, Firecrawl engine exhaustion, and application HTTP 500.

The error-phrase regression also protects legitimate vacancy text: a
substantive AI Engineer description beginning “When something went wrong in
production…” remains acceptable when it has sufficient positive job evidence.
A generic “Something went wrong” page heading and the confirmed HTTP 200
internal-error reproduction remain rejected. This narrows the phrase-level
false-positive rule while retaining the broader page-error, access-denied,
not-found, hosting-error, and challenge checks.

## Career-page-only source eligibility

Discovery admits direct-posting URLs only from explicitly enabled ATS
providers with supported posting paths or explicitly registered employer
career hosts and path rules. The default ATS hosts are Greenhouse, Lever,
Ashby, and SmartRecruiters. Workday tenants and employer career hosts require
explicit registration; none are registered by default.

Eligibility is enforced before canonical deduplication and candidate-cap
admission. SearXNG `site:` hints improve search relevance but are not the
eligibility boundary. Malformed, credential-bearing, unsupported, listing,
landing-page, and oversized URLs are rejected before crawling. Vacancy
acceptance uses page-derived evidence; search-result titles and snippets
cannot supply missing mandatory vacancy fields. Failed or unusable crawls do
not create new vacancies, and failed recrawls preserve previously accepted
vacancy content and verification state.
