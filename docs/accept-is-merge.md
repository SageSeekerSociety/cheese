# Accepting a card merges its pull request

A card displays a task's pull request. Accepting calls the project's forge to
merge the exact revision the reviewer saw. Cheese supplies the review panel;
GitHub or Forgejo stores the code and records the merge.

## Repository ownership

Each project selects one forge. New projects default to the deployment's
Forgejo. A project selecting GitHub may wait for its repository connection;
it cannot deliver changes until that connection exists. Connected GitHub
projects keep GitHub as their authoritative repository.

Task machines clone, commit and push to that repository. Machines without
direct access use the authenticated relay. The backend does not maintain a
checkout for reading project source, merging changes or pushing on a machine's
behalf. Committed file views read the forge; unfinished file views read the
task machine.

## From work to acceptance

1. A task declares its branch and base. The machine pushes its first changes.
2. `pr_publish.sweep_draft_prs` observes a branch ahead of its base and opens a
   draft pull request for the task. Subsequent pushes update that request.
3. `cheese ready` removes the draft state. Filing an acceptance card also makes
   the request ready for review; marking it ready alone does not create a card.
4. The panel displays the changes, checks and review state. The agent runs its
   own checks; any forge-hosted checks run on that forge's configured runners.
   Cheese reads their results.
5. Acceptance checks the reviewer, required approvals, project policy and the
   revision shown in the browser, then calls the forge's merge API.
6. A successful merge records delivery and closes the task. The room remains
   active for further work.

The pull request belongs to `Task.pr_number`. Cards refer to that task's request;
revising a card does not create another request. `cheese push-fix` lets the
machine publish another revision. The poller never commits a working tree or
pushes its contents.

## Review and merge policy

Where the forge enforces repository protection, Cheese respects its verdict.
Where it does not, Cheese applies the project's `branch_protection` settings.
Hosting a project does not require changing its repository settings.

Required checks can be scoped to changed paths. A required check that is absent
or still running blocks acceptance. Optional failed checks do not themselves
make a check required. The project's required-check roster defaults to empty.

Acceptance, merge-anyway and arming auto-merge each carry the revision displayed
in the browser. The server checks it against the card, reads the current request
from the forge and passes the reviewed revision to the merge operation. A newer
push must not silently replace the reviewed work. New commits dismiss approvals
when `dismiss_stale` is enabled and disarm the corresponding auto-merge approval.

Auto-merge is an explicit per-card choice, available when the project permits
it. Once the rules hold, the merge records the person who armed it. An override
through `POST /accept-cards/{id}/merge-anyway` requires membership in the
project's override roster and records the actor, check state and reason.

`AcceptService` applies shared actor, vote and revision checks before invoking
the selected provider. `review/forge.py` resolves the persisted project binding
to GitHub or Forgejo. A missing or unreadable binding cannot select a local
merge implementation. A card read can display unknown capabilities; acceptance
requires a usable binding.

## Events and reconciliation

Forge events trigger reconciliation. A deployment can receive forwarded events
over an outbound connection; periodic polling also reconciles state after a
missed event. Both paths observe forge state before recording an outcome.

`SchedulerService.poll_open_prs` and `AcceptService.advance_pr_card` reconcile
pending cards and returned deliveries. They update checks and merge status,
dismiss stale approvals, notify the agent about work such as failed checks or
conflicts, and merge explicitly armed cards when their requirements hold.

An external merge accepts a pending card. For a returned card, reconciliation
records the task's delivery while preserving the return decision, reviewer and
reviewed revision. A request closed without merging remains unaccepted.
Comments from the forge appear with their source; panel review is also recorded
on the forge.

## Dependent tasks

The agent can declare that a task starts from another task's branch. Its pull
request then compares against that branch, so the review shows the child's own
changes. People do not select task dependencies through a separate control.

When the parent task closes, reconciliation records a durable instruction for
the child agent and clears pending card approvals and auto-merge authorization.
If the parent delivered, the child's pull request is retargeted to the parent's
base. The agent must then fetch, reconcile its changes, resolve conflicts, run
checks and push again. Retargeting alone does not rewrite commits, particularly
when the parent was squash-merged.

If the parent closed without delivery, the child keeps its work and receives an
instruction to reassess the dependency. Closing a parent does not establish
that its changes were merged, and does not automatically close its children.
