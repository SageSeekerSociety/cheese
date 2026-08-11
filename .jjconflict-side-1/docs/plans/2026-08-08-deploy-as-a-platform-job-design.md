# Deploy as a Platform Job Design

## The question this answers

What is `scripts/on-dogfood-push.sh`? It lives in the target repository, the
platform discovers it by convention and starts it detached, and the platform's
promise to the user — 采纳即上线 — is only true if it succeeds. It is neither a
project's private script nor a platform component, and every problem the
dogfooding agent has reported about deployment follows from that ambiguity.

## The precedent already in this codebase

The quality gate answered the same question correctly. A project supplies a
command in `project.settings["check_command"]`; the platform runs it in a
disposable container with a 600-second ceiling, writes the full output to
`logs/gate-<topic8>.log`, persists the tail plus a timestamp on the card
(`gate_output`, `gate_passed_at`), and moves the card to `pending` or
`gate_failed`. The project decided *what* to check. The platform owns *when it
runs, how long it may take, where its output goes, and what its result means*.

Deployment inverted that. The project's script owns the merge, the checks, the
rollback and the redeploy; it invents its own lock in `.git/dogfood-push.lock`;
it discards the check output to `/dev/null` before deciding to roll back; and
the platform holds no record that any of it happened. `push_back` starts it with
`Popen(..., start_new_session=True)` and returns, so there is no exit code to
collect and no process to supervise.

The two paths differ in almost every property, and the difference is not a gap
in features. It is a difference in who owns execution.

## The rule

**The platform owns the contract; the project owns the recipe.**

The contract is: when a deploy runs, that only one runs at a time, how long it
may take, how failures are classified and retried, what state it is in, where
its log lives, what its history was, and who may intervene. The recipe is which
commands mean "check" and "deploy" for this repository.

Applied here, the orchestration in `on-dogfood-push.sh` — locking, merging,
rollback, timeout, sequencing — comes back to the platform, and what remains
project-supplied is the commands, configured the way `check_command` already is.
A repository that configures nothing still deploys nothing; the zero-config
import rule is unaffected.

## What the reported problems become

Nine items were raised. Seven of them are consequences of the ownership
inversion and cease to exist once execution moves; two are real work.

Pipeline state, full logs, and the run history are what a `DeployRun` record is:
one row per run with status, branch, topic, timestamps, and a log path — the
same shape `gate_output`/`gate_passed_at` already have, for the same reason.

The request for a process-level snapshot of the lock holder — `/proc/<pid>`
status bits, `wchan`, the child process tree — is the sharpest evidence of the
inversion. It asks the platform to diagnose a foreign process by autopsy,
because that process is the only thing that knows what is happening. A platform
that owns the job knows its own task's state and does not need to infer it from
kernel wait channels.

"Clean up this stuck push" becomes an operation on a job the platform owns, so
its audit trail (who, when, which run) is a natural consequence rather than a
feature to add. The self-healing watchdog becomes a reaper over the platform's
own jobs, which also dissolves the hardest part of that request: there is no
need to grant a supervisor narrow permission to `kill` processes matching a
pattern, because it is cancelling its own task rather than killing a stranger's.
A timeout on `run_checks()` is simply the job's timeout, which the gate has had
all along.

Two remain. The self-hosted runner's health is genuinely separate information
and belongs on the same status surface, so an agent can see it without a GitHub
token. And failure classification — distinguishing infrastructure noise (DNS,
network, a registry that will not answer) from a real red check — is real logic
that must not be reinvented: it exists in the gate path today and does not exist
in `run_checks()`, which is why the same root cause reappears in the second
place. Sharing one executor is what makes that logic apply once.

## Migration

The step already taken is `dogfood_notices.watch_dogfood_push`: the platform now
waits on the detached process and posts the outcome (deployed, rolled back,
conflict, unknown) into the topic. It is the right direction and insufficient by
construction — it observes a process it does not control, and it covers only the
local-path hook mode, not the GitHub Actions path the dev deployment actually
uses. That gap is why a runner that died on 2026-08-07 left the pipeline queued
silently for twenty-five hours with nothing to report it.

The order that keeps each step verifiable: give the deploy a record before
changing who runs it, so the current pipeline's behaviour becomes visible.
Then move execution into the same runner the gate uses, with the project's
commands as configuration, keeping the script as a fallback until the new path
has run a full dogfooding cycle. Then delete the script, its lock, and its log
file, because two mechanisms for the same job is how the classification logic
came to exist in only one of them.

## Out of scope

How the GitHub Actions path reports back (the dev deployment's real route today)
is a related but separate question: the platform can own a job whose recipe is
"dispatch a workflow and wait", but the waiting and reporting differ enough from
a local command to deserve their own design.
