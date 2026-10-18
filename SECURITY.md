# Security policy

## Safe use

- Keep API keys, database URLs/passwords, `.env` files, session tokens, and private user data out of Git, logs, screenshots, and public issues.
- Use the tracked environment examples as templates and set real values only in local ignored files or a private runtime environment.
- Use synthetic accounts, job records, and resumes when testing. Do not upload a real resume unless you understand that extracted text is stored in the configured PostgreSQL database and may be sent to Gemini when resume-based AI features are used.
- Keep provider credentials server-side. Do not put them in frontend `VITE_*` variables, which are embedded in the browser build.
- Test database fixtures are destructive. Point `TEST_DATABASE_URL` only at a disposable PostgreSQL instance and test database.

## Reporting a vulnerability

Please do not publish an exploitable vulnerability or personal data in a public issue. If GitHub private vulnerability reporting is enabled for this repository, use it. Otherwise, contact the repository maintainer through a private contact method listed on the maintainer's GitHub profile. This repository does not publish a separate security email address.

Include a concise description, affected files or endpoints, impact, and safe reproduction steps. Do not include live credentials or real user records. If a credential was exposed, revoke or rotate it immediately before reporting; removing it from a later commit does not make an exposed secret safe.
