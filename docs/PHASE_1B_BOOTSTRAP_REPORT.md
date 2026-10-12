# Phase 1B Bootstrap Report

## Summary

- **Migration:** applied successfully. Database is now at `20261012_0003 (head)`
  (was `20261003_0002`).
- **Legacy-owner bootstrap:** **BLOCKED** — not completed. The owner account does
  not exist and creating it requires a password that cannot be supplied in this
  session without exposing it. No password was invented and no account was
  created. Details below.
- **Data integrity:** all legacy records preserved; no duplicates; nothing
  reassigned. The database is in a safe, consistent state.

## Environment (preflight)

- Repository root: `C:/Users/longt/Desktop/projects/ai-career-copilot`
- Branch: `feat/database-models`
- Database: PostgreSQL via `postgresql+psycopg` (psycopg3), database
  `career_copilot` on `localhost:5432`. Credentials were read from the
  application settings and are deliberately not reproduced here.

### Preflight findings (before migration)

- Alembic revision: `20261003_0002`.
- `users` and `sessions` tables: absent (as expected).
- `candidates.user_id` column: absent (as expected).
- Existing candidates: **exactly one** — `5d9f9f21-f812-4eff-9dc6-4ac0d6ad1b91`.
- Existing accounts: none.

Because there was exactly one ownerless candidate, the legacy data was
**unambiguous** and preflight permitted proceeding.

### Migration safety review

`20261012_0003_add_authentication` is purely additive: it creates `users` and
`sessions`, adds a **nullable** `candidates.user_id` column, and creates indexes
and a foreign key. It performs no `DROP`, `TRUNCATE`, reset or recreate. Safe for
the existing database.

## Migration result

Command run from `backend/` with the project venv:

```
python -m alembic upgrade head
```

Result: `Running upgrade 20261003_0002 -> 20261012_0003, add authentication`.
`alembic current` now reports `20261012_0003 (head)`. No servers or containers
were restarted.

## Bootstrap result

**BLOCKED.** The supported CLI is `python -m app.cli.bootstrap_legacy_owner`.
Its read-only plan for the intended owner was:

- intended owner: `longtsu59@gmail.com`
- account: does not exist (will be created)
- legacy candidate: `5d9f9f21-f812-4eff-9dc6-4ac0d6ad1b91` (will be reused)
- unassigned candidates: 1

Creating the owner account requires a password. The CLI accepts either an
interactive prompt (`getpass`) or the `--password` command-line argument. In this
execution environment:

- There is **no interactive TTY** — a probe of the CLI's `getpass` prompt hung
  until it was terminated, so the interactive prompt cannot be satisfied here.
- Passing the password via `--password` would place the secret in a logged
  command line, which is explicitly prohibited.
- Inventing a password is prohibited, and there is no existing account whose
  credentials could be preserved.

Per the task's instruction not to expose a password, the bootstrap was stopped
and this limitation is reported instead. As evidence that no partial state was
left behind, invoking the service with `confirm=True` and no password was
rejected with `BootstrapError` before any write; `users` remained empty and the
legacy candidate remained unassigned.

### Operator action to complete the bootstrap

Run the following in a real terminal (interactive password prompt, then a
confirmation prompt). Do not pass `--password`:

```
cd backend
.\.venv\Scripts\Activate.ps1
python -m app.cli.bootstrap_legacy_owner --email longtsu59@gmail.com
```

The command will prompt for a password for the new account, show the plan
(create account, reuse the single legacy candidate), and ask for confirmation.

## Preservation and consistency checks

Counts before migration (revision `0002`) and after (revision `0003`):

| Entity                        | Before | After |
| ----------------------------- | -----: | ----: |
| candidates                    |      1 |     1 |
| documents (`master_resume`)   |      1 |     1 |
| documents (`cover_letter`)    |      1 |     1 |
| matches                       |      1 |     1 |
| applications                  |      2 |     2 |
| job_analyses                  |      2 |     2 |
| agent_runs                    |      9 |     9 |
| jobs                          |     17 |    17 |
| users                         |    n/a |     0 |
| sessions                      |    n/a |     0 |

Relationship checks (after migration):

- `candidates`: 1 row, `user_id = NULL` (still ownerless — correct, awaiting
  bootstrap).
- `documents.candidate_id`: single distinct value
  `5d9f9f21-f812-4eff-9dc6-4ac0d6ad1b91` — unchanged.
- `matches.candidate_id`: single distinct value
  `5d9f9f21-f812-4eff-9dc6-4ac0d6ad1b91` — unchanged.
- No duplicate candidate was created (candidate count stayed at 1).
- No other user's records were reassigned (there are no other users).

Note: `applications` has no candidate/owner column yet (ownership enforcement is
Phase 1B/1C scope) — unchanged by this migration.

## Blockers

1. **Owner account cannot be created without a password.** The execution
   environment is non-interactive (no TTY), so the CLI's secure interactive
   prompt is unusable, and the only alternative (`--password`) would expose the
   secret in a logged command. This is the sole reason the bootstrap did not
   complete.
2. **Legacy data remains unassigned** until the operator runs the command above.
   This is a safe intermediate state: the assignment column is nullable, no
   account exists, and no data was modified.

## Boundaries honored

- No application-code or migration-file edits.
- No frontend changes.
- No AI/Gemini/Firecrawl/SearXNG or external API calls.
- No full test suite run.
- No commits, staging, resets, stashing or discards; existing working-tree
  changes preserved.
- No destructive database operations; no servers or containers restarted.
- No passwords, hashes, tokens, resume contents or credentials printed.
