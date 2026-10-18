# Synthetic job-matching benchmark

This benchmark uses nine fictional resumes and job analyses. It is separate
from the application and normal test suite. It does not run at startup and
pytest never calls Gemini.

## Cases and rubric

- **Strong match:** the resume documents the listed Python, PyTorch, and
  Transformers requirements. The verdict must be `strong_match` or
  `good_match`, and its score must rank above the partial, essential-skill-gap,
  and unrelated-role cases. Because this job has no education requirement and
  the candidate evidence contains no education information,
  `education_match` must be `unclear`.
- **Partial match:** Python and SQL are evidenced but Kubernetes is absent.
  Kubernetes must appear as a required gap; verdict is `partial_match` or
  `weak_match`. Its score must rank above the essential-skill-gap and unrelated
  cases.
- **Missing essential skills:** only Python is evidenced among requirements;
  PyTorch, CUDA, and distributed training must be reported missing. Verdict is
  `weak_match` or `poor_match`. The job's generic 5+ years requirement is met
  by the candidate's five reported years, so `experience_match` must be
  `matched`; missing production GPU-training experience remains a separate gap.
- **Unrelated role:** an accounting resume is compared with an ML platform
  role. Core ML requirements must not be claimed as strengths/matched skills;
  verdict is `weak_match` or `poor_match`.
- **Certification not mentioned:** the resume gives no certification
  information. The required certification is not evidenced; the output must
  not claim it is held or assert that it is confirmed absent.
- **Certification confirmed absent:** the resume explicitly states that the
  candidate does not hold a required certification. The output may describe
  it as absent and must identify it as a gap.
- **Preferred skill not evidenced:** a required skill is supported while a
  preferred skill is not mentioned. Report the preferred skill as
  `missing_preferred_skills` and describe the evidence as absent from the
  supplied resume, not as proof the candidate cannot do it.
- **Relevant experience gap:** the candidate has sufficient total professional
  tenure but the job explicitly requires relevant ML platform experience and
  the resume establishes that the candidate's work was in an unrelated field.
  Total tenure must not be substituted for relevant experience.
- **Missing evidence:** the resume supports NLP skills but gives no education
  history or employment dates. Education, experience, and seniority signals
  must be `unclear`, not presumed matched or mismatched.

## Evidence-aware interpretation

- **Supported** means the supplied resume/profile contains direct evidence or
  an unambiguous equivalent. A listed skill is evidence of that skill; it is
  not automatically evidence of a certification or years using the skill.
- **Not evidenced** means the supplied material does not mention or establish
  a qualification. Describe it as “not evidenced,” “not listed,” or similar.
  Do not turn silence into a claim that the candidate lacks the qualification.
- **Confirmed absent** requires explicit negative evidence in the supplied
  material (for example, the candidate states they do not hold a named
  certification). Keep that distinct from a resume that simply omits it.
- **Total versus relevant experience:** compare total professional years to a
  generic total-years requirement only when that is what the job asks for. If
  the job explicitly requires relevant-role experience, total years alone do
  not satisfy it. Explicitly unrelated work can support a relevant-experience
  mismatch; when role relevance or dates cannot be established, use `unclear`.
- **Required certifications and other mandatory qualifications:** if not
  mentioned, report an evidence gap without asserting fulfillment or absence.
  If explicitly absent, identify the missing mandatory qualification. The
  output contract has no dedicated certification signal, so the distinction is
  expressed in bounded gap/rationale text and remains subject to human review.
- **Unknown mandatory evidence:** do not call the requirement satisfied or
  failed; retain `unclear` where a matching signal exists and identify the
  verification gap. The existing rubric does not define how this uncertainty
  must change the overall verdict or numeric score.

## Open rubric decisions

The benchmark intentionally does not invent numeric score bands or verdict
cutoffs. The production prompt defines a 0–100 score but no calibration
anchors. Before adding score-ordering constraints for certification or
missing-evidence cases, decide whether an unknown or confirmed-absent mandatory
qualification is a hard blocker, how it affects verdict vocabulary, and how
much it should affect the score. Also decide whether `experience_match` means
total tenure, relevant tenure, or the job's stated requirement; the current
output has no separate total/relevant experience signals. Until those policy
choices are made, these cases check evidence representation and leave verdict
and score effects for human review.

No absolute score bands are asserted because the production prompt defines a
0–100 scale but does not define category cutoffs or calibration anchors. The
benchmark only checks the explicit pairwise ranking constraints above.

## Deterministic checks vs. human review

The evaluator checks schema-valid outputs, selected verdicts, matched and
missing required/preferred skill labels, required signal states, evidence
language near named qualifications, selected forbidden positive claims, gap
mentions, and declared pairwise score ordering. Positive-claim checks cover
the structured positive fields and a narrow set of common verb constructions
in rationale text. Evidence-language checks use nearby cue phrases. These are
reproducible lexical checks, not semantic entailment or a general negation
detector. They can miss paraphrases or misread scope; ambiguous wording,
strengths, gaps, rationales, and credentials still require human review. The
checks do not establish general model accuracy or score/confidence calibration.

## Explicit model evaluation

From `backend/`, configure `CAREER_COPILOT_GEMINI_API_KEY` and optionally
`CAREER_COPILOT_GEMINI_MODEL` in the normal backend environment, then run:

```powershell
.\.venv\Scripts\python.exe -m evaluation.run_job_matching --confirm-provider-calls
```

The acknowledgement flag is mandatory. A complete run makes exactly nine
Gemini requests, one per case. The existing client configures a single attempt
per request; the runner stops on the first failure and does not retry. Model
calls may incur provider charges or consume quota. Do not use real resumes or
other personal data with this benchmark.

The failed local run recorded `gemini-3.5-flash`, while the project default
and `backend/.env.example` specify `gemini-3.8-flash`. As checked on
2026-10-19, Google marks Gemini 3.5 Flash as deprecated and says requests to
that identifier are automatically routed to Gemini 3.6 Flash. This
configuration discrepancy does not establish the cause of the failed run; no
model setting was changed. See
[Google's Gemini 3.5 Flash migration note](https://ai.google.dev/gemini-api/docs/whats-new-gemini-3.5).

Each run writes a new timestamped JSON file under
`backend/artifacts/job-matching-evaluations/`. That directory is gitignored.
The runner reserves the unique result file before the first provider call, so
an unwritable output directory or timestamp collision stops the run before
incurring request cost. Existing files are never overwritten. The artifact
records UTC timestamps, model, prompt version, per-case prompt hash, structured
output, deterministic checks, and safe failure metadata: an error category,
an optional validated HTTP status, and an optional allowlisted provider status.
It omits prompts, API credentials, response bodies, and provider exception
messages.
