# Synthetic job-matching benchmark

This benchmark uses six fictional resumes and job analyses. It is separate
from the application and normal test suite. It does not run at startup and
pytest never calls Gemini.

## Cases and rubric

- **Strong match:** the resume documents the listed Python, PyTorch, and
  Transformers requirements. The verdict must be `strong_match` or
  `good_match`, and its score must rank above the partial, essential-skill-gap,
  and unrelated-role cases.
- **Partial match:** Python and SQL are evidenced but Kubernetes is absent.
  Kubernetes must appear as a required gap; verdict is `partial_match` or
  `weak_match`. Its score must rank above the essential-skill-gap and unrelated
  cases.
- **Missing essential skills:** only Python is evidenced among requirements;
  PyTorch, CUDA, and distributed training must be reported missing. Verdict is
  `weak_match` or `poor_match`.
- **Unrelated role:** an accounting resume is compared with an ML platform
  role. Core ML requirements must not be claimed as strengths/matched skills;
  verdict is `weak_match` or `poor_match`.
- **Unsupported qualification:** the candidate has cloud project evidence but
  no evidence of the required named certification. The certification must not
  be listed as a positive skill/strength and must be identified as a gap.
- **Missing evidence:** the resume supports NLP skills but gives no education
  history or employment dates. Education and experience signals must be
  `unclear`, not presumed matched or mismatched.

No absolute score bands are asserted because the production prompt defines a
0–100 scale but does not define category cutoffs or calibration anchors. The
benchmark only checks the explicit pairwise ranking constraints above.

## Deterministic checks vs. human review

The evaluator checks schema-valid outputs, allowed verdicts for selected
cases, exact expected matched/missing skill labels, required `unclear` signals,
selected forbidden positive qualification phrases, required gap mentions, and
declared pairwise score ordering. These checks are reproducible but lexical.
They do **not** establish that free-text rationale, strengths, or gaps are
factually grounded, nor that scores or the self-reported confidence are
calibrated. Review those outputs manually. Treat paraphrases, skill
equivalence, rationale quality, and ambiguous transferability as human review
items rather than deterministic pass/fail rules.

## Explicit model evaluation

From `backend/`, configure `CAREER_COPILOT_GEMINI_API_KEY` and optionally
`CAREER_COPILOT_GEMINI_MODEL` in the normal backend environment, then run:

```powershell
.\.venv\Scripts\python.exe -m evaluation.run_job_matching --confirm-provider-calls
```

The acknowledgement flag is mandatory. A complete run makes exactly six
Gemini requests, one per case. The existing client configures a single attempt
per request; the runner stops on the first failure and does not retry. Model
calls may incur provider charges or consume quota. Do not use real resumes or
other personal data with this benchmark.

Each run writes a new timestamped JSON file under
`backend/artifacts/job-matching-evaluations/`. That directory is gitignored.
The runner reserves the unique result file before the first provider call, so
an unwritable output directory or timestamp collision stops the run before
incurring request cost. Existing files are never overwritten. The artifact
records UTC timestamps, model, prompt version, per-case prompt hash, structured
output, deterministic checks, and a generic failure type if a call fails. It
omits prompts, API credentials, and provider exception text.
