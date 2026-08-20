# djobs Project Mode (optional ARUN)

`djobs` has two intentionally separate layers:

1. **Memory + coordination (always available)** — repository-scoped memory, recovery capsules,
   checkpoint/handoff, leases, and cross-agent ownership.
2. **Durable project completion (optional)** — ARUN owns goals, failure/recovery state, scope guards,
   evidence coverage, hard acceptance, and verifier-gated completion.

Project Mode connects those layers without copying ARUN into djobs and without making ARUN depend on
djobs.

```text
coding agent / current reasoning model
            |
            +---- djobs ---- memory, recall, checkpoint, handoff, leases
            |
            +---- ARUN ----- durable goal/recovery/scope/acceptance state
                         (optional external executable)
```

ARUN remains executor-neutral. `djobs project` never starts another reasoning model or executor.
`djobs project next` only asks ARUN for the next external-control packet; the current agent/executor
still performs the actual repository/tool work and supplies evidence back through ARUN's control
contract.

## Quick start

Normal memory remains unchanged:

```bash
djobs setup
djobs doctor
djobs memory list
```

When ARUN is installed and a task needs durable completion semantics:

```bash
djobs project doctor

djobs project init \
  --objective "Fix the OAuth callback loop" \
  --constraint "Do not change the public API" \
  --acceptance "Focused callback tests pass" \
  --acceptance "No unrelated files change"

djobs project status
djobs project next
```

`project init` refuses to create a second ARUN project for the same canonical root. This prevents
accidental split-brain durable state.

`project status` resolves the project by canonical repository root and returns ARUN's authoritative
status output.

`project next` is explicit because it creates or resumes one bounded ARUN external-control turn. It
does **not** execute the turn. Continue with the ARUN external-control protocol (`baseline`, `plan`,
`guard`, external execution, `finish`, and verification) using the same current reasoning model.

## Installation boundary

ARUN is optional and is not installed as a transitive djobs dependency. If it is absent:

```bash
djobs project doctor
```

reports Project Mode as unavailable while normal djobs memory and coordination continue to work.
This keeps the default install local, lightweight, and fail-open.

If the executable is not named `arun`, point djobs at it with:

```bash
DJOBS_ARUN_COMMAND=/path/to/arun djobs project doctor
```

The value is treated as one executable path/name, not shell syntax.

## Authority and safety

- djobs memory is **context**, never instruction authority.
- checkpoint/handoff provide explicit ownership; passive memory never claims work.
- ARUN Project JSON is authoritative for durable project completion state.
- djobs does not reimplement ARUN ledgers, scope guards, promotion gates, or verifier semantics.
- no second LLM, planner, worker, reviewer, or supervisor is started by Project Mode.
- subprocess calls use argv execution without `shell=True`.
- ARUN command failures are surfaced once; Project Mode does not hide them with retries.

## Why this exists

Memory alone answers: **"What happened before?"**

Coordination answers: **"Who owns this bounded work?"**

Project Mode adds: **"What is the goal, what failed, what evidence is sufficient, and is the work
actually complete?"**

The product promise becomes: **remember, coordinate, and finish work safely across AI sessions**.
