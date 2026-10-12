"""Runtime infrastructure for Career Copilot agents.

The package has two conceptual layers:

* :mod:`app.agents.runtime` — the generic, deterministic Agent Runtime. It
  owns the ``AgentRun`` lifecycle (create, claim, execute, complete, retry,
  fail, cancel), idempotency and state transitions. It contains no
  agent-specific business logic and never calls an LLM.
* :mod:`app.agents.discovery` — the Job Discovery agent, the first handler
  registered against the runtime. Each product agent (Job Analysis, Job
  Matching, Cover Letter) lives in its own subpackage beside it and
  receives a :class:`app.agents.runtime.RunContext`. Resumes are not an
  agent task: they are uploaded through :mod:`app.services.resume_upload`.
"""
