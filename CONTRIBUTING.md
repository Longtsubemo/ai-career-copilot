# Contributing

Thanks for helping improve AI Career Copilot. Keep changes focused, explain the user-visible or reliability benefit, and avoid adding real provider calls to tests.

## Local development

Follow the [README setup](README.md#local-setup-windows-powershell). Backend commands run from `backend/`; frontend commands run from `frontend/`.

Before opening a pull request:

1. Add or update deterministic tests. Prefer fake clients and mocked HTTP transports; tests must not call Gemini, Firecrawl, SearXNG, or job sites.
2. Run backend tests against a disposable PostgreSQL database. Some fixtures create scratch databases and drop/recreate tables. Never use a production or valuable development database for tests; see the warning and `TEST_DATABASE_URL` example in the README.
3. Run the frontend checks with `npm test` and `npm run build` when frontend code changes.
4. Run `git diff --check` and review the final diff for secrets, real user data, and generated files.

## Pull requests

- Describe the problem, the approach, and any remaining limitations.
- Include test commands and actual results; identify tests that were not run.
- Use synthetic resumes and provider fixtures. Do not attach real resumes, credentials, session data, or database exports.
- Keep migrations aligned with SQLAlchemy models and include migration coverage when schema changes are made.
- Do not broaden the supported job-source registry or change provider behavior without documenting and testing the effect.

There is no separate code-style or lint configuration at present. Match nearby code and keep behavior explicit and testable.
