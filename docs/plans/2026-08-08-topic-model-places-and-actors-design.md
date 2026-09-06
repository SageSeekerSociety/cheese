# The topic model: places and actors

This started as a discussion document and ended up with answers. It exists
because three separate requests turned out to be one modelling question, and the
question was worth settling before anyone wrote code against it.

The three requests: an ops 芝士 that keeps working indefinitely and remembers
things for months; other people and systems delivering work into that agent
without knowing which topic it lives in; and one agent telling another agent
something. None of them fit today, and none of them are missing a feature. They
are all asking where the boundary is between a place and the thing that acts in
it.

The second half of this document — from "Four layers" onward — is the settled
part. The first half is how we got there, including two claims from the first
draft that turned out to be wrong.

## What a 话题 is today

Four things at once, with four different natural lifetimes.

It is a **room**: it has a member roster with roles, `@all`/`@here` reach that
roster, and the model's own comment says so — "a topic is a group room and its
membership governs who @all/@here reaches."

It is a **task**: it carries an accept card, gets accepted, and is archived.

It is a **workspace**: it owns a git worktree, a branch, and a container.

And it is the **agent's session boundary**: the tmux session is created for it
and dies with it. The provider states this as the design — "the tmux session IS
the continuity."

A room outlives its work. A task ends. A workspace can be released and rebuilt.
A session is a process. Collapsing all four into one object means the shortest
lifetime wins: when the task is accepted, the room, the workspace and the memory
go with it.

That is the whole bug behind "采纳之后什么都追不到了". Not a missing feature —
five things sharing one lifetime.

## What the references actually do

Both references were read at the source rather than from their marketing pages,
because the interesting parts are in the implementation.

### buzz

The vocabulary is explicit. A **community** is the workspace scoped to a
URL/relay — a tenant boundary, not a place you talk in. A **channel** is a room,
and channels are siblings with no relation to one another. A **thread** is a
nested conversation inside a channel with no roster of its own. Everything
anyone does — every message, reaction, workflow step, review approval, git event
— is one signed event in one log, and that log is the record.

Agents are "members, not bots": each holds its own keys and its own channel
memberships, and you add an agent to a channel the same way you add a person.

Three implementation details matter more than the vocabulary.

**Sessions are per channel, and the process is a pooled resource.** `buzz-acp`
owns N `AcpClient` instances, each a real subprocess. Its module doc is blunt
about ownership: the client is not `Clone`, so a claim *moves* the agent out of
its slot and returning puts it back. Inside an agent, sessions are indexed by
channel — a plain `channel_id → session_id` map — so one process carries several
channels' conversations at once. Dispatch is affinity-first: the pool prefers an
idle agent that already holds a session for this channel, and there is a
`has_session_for()` helper purely to compute the affinity hit.

Two consequences follow, and the second one is the load-bearing one:

- Mentions in *different* channels run in parallel; mentions in the *same*
  channel queue, at most one prompt in flight, and the queued events are batched
  into a single prompt rather than dropped.
- When affinity misses, the next turn for that channel lands on a different
  process and starts a **new** session. So buzz does not treat the transcript as
  the continuity even within one room. It cannot — the transcript is disposable
  by construction.

**Memory is attached to the key, not the transcript.** The agent's `core` engram
(NIP-AE, encrypted to the agent↔owner pair) is fetched once when a session is
born and rendered into the prompt under an `[Agent Memory — core]` header. The
fetch has one defensive rule worth stealing outright: on a transport or parse
error it injects *nothing*, because treating a relay outage as "no memory" would
invite the agent to overwrite real-but-unreachable memory with a fresh profile.

**The place's document is a separate section.** Alongside core, the harness
renders a `[Channel Canvas]` section — the channel's collaborative document, not
a second memory store. Both are populated once before session creation, never
refreshed mid-session, and cleared together on invalidation. So what a new
session starts from is: *my memory* plus *this place's document*.

**Identity travels, state does not.** An agent's profile, presence, DMs,
memories, jobs, channel memberships, and audit trail are all scoped to the
community behind the relay URL. The same npub can join another community, but it
arrives carrying the key and nothing else. buzz also splits *configuration* from
*state* explicitly: a persona (`kind:30175`) describes what an agent should be,
an engram records what it has learned.

### Claude Tag

Scoping is by channel and thread, and the session model is stated plainly.

**A session belongs to a thread, not a channel.** Two threads in the same
channel are two separate sessions with separate sandboxes and no shared state.
The lifecycle is five steps: someone tags Claude in, a sandbox builds *for that
thread*, the working loop runs, the result lands in the thread, and then a quiet
period releases the sandbox — which the next reply rebuilds.

**The thread is durable; the sandbox is not.** What survives an idle period: the
conversation and its context, channel memory, and anything pushed, posted, or
opened as a PR. What does not: files that exist only in the sandbox.

This is not a session that restarts from zero — the session carries the whole
conversation into every reply, and a thread that accumulates too many tasks
eventually outgrows what one session can hold. Container disposable, conversation
not.

The practical consequence is a rule the docs repeat in three places: on long
work, push branches and post drafts *as you go*. For us that is not a habit
recommendation, it is an architectural constraint — the workspace is not
guaranteed to be there next turn.

**Memory follows places, and public channels share it.** This corrects the first
draft of this document, which claimed separate channels keep separate memories.
They do not: memory generated in public channels is shared across the workspace,
so a decision recorded in one channel is available when someone asks in another.
Only private channels are isolated — they read workspace memory but write to
their own store, and that store does not move with them if the channel is later
made public.

Memory is also explicitly *not* a transcript. It is a curated note; the guidance
is to keep entries short because long ones crowd everything else out, and to put
playbooks in a repository the agent can read instead. Alongside memory sit four
other places to put knowledge — `CLAUDE.md`, channel instructions, skills, and
scope-level custom instructions — each with a different writer and reach. Only
one of the five is called memory.

### Claude Code

Worth reading carefully, because our first draft treated its subagents as
fire-and-forget and that is wrong. There are four distinct things:

| | Context | Where output goes | Who can address it | Shared place |
|---|---|---|---|---|
| Subagent | own window | back to the caller | caller, plus named siblings via a roster | none |
| Background agent | own session | own transcript | you, from one panel | none |
| Cross-session messaging | own session | own transcript | other sessions, by name | none |
| Agent team | own session | own transcript | lead and any teammate | shared **task list** |

Subagents can be resumed and sent follow-up messages with their context intact;
a background subagent's permission prompts surface in the main session, where
you can approve or deny a single tool call without killing it. Agent teams go
further — each teammate is a full independent session, and you can open its
transcript and message it directly, or interrupt its turn, without going through
the lead. Any teammate can message any other by name.

And yet: to reach everyone on a team, you send one message per recipient. The
shared object is a **task list**, not a conversation. Each teammate's words stay
in its own transcript, so a participant who joins later sees nothing. Mailboxes
are per-agent JSON files under the team directory, scoped to the session and
cleaned up when it ends.

Three of its documented limitations are directly instructive:

- **One team per session, scoped to that session.** A room cannot be built on a
  session object; it has to be a platform object.
- **Teammates cannot spawn teammates.** Nesting stops at one level. Our decision
  below to make tasks non-nestable is the same choice, not a compromise.
- **The lead is fixed for the session's lifetime.** A room should not have a
  fixed owner. Our roster-and-roles membership is the more flexible model; keep
  it.

## The layer counts line up after all

The first draft claimed cheesex has four levels against the references' three,
and called the extra level suspicious. That was a mis-mapping. The levels
correspond:

| buzz | cheesex | What it is |
|---|---|---|
| relay membership | 团队 | who may enter — an org boundary, not a place you talk in |
| community | 项目 | tenant boundary: repository, compute, memory scope |
| channel | 话题 | a place you talk |
| thread | 子话题 → task | one piece of work |

团队 is not a fourth *place*; it is the membership list, which buzz also has (a
relay member table with an operator CLI) without making it a level you can speak
in.

So 项目 is load-bearing, and it is load-bearing in a specific way: **项目 is our
community.** That settles where memory is scoped, below.

What does *not* line up is the shape. In Slack and buzz the levels are a
boundary, a room, and a sub-conversation: you cannot talk in the boundary, rooms
are a flat set with no relationship to each other, and a thread has no roster or
lifecycle of its own. In cheesex all three lower levels are rooms nested in
rooms — a subtopic has its own roster, worktree, session and accept card, which
makes it a full room rather than a thread — and the nesting carries task
decomposition semantics.

That is why lateral delivery has nowhere to live. In a decomposition tree the
only meaningful relations are parent and child, so we grew three separate
one-way mechanisms — `<#topic>` references, `cheese notify`, and conclusion
return-flow — each a special case of a general thing that does not exist.

## The missing axis

The gap is not a level of nesting. It is a second kind of entity.

cheesex models one axis — **places**: project, topic, subtopic. The agent is a
property of a place, expressed as a container bound to it.

Both references model two: places, and **actors** that join places. An actor has
its own identity and its own memory; being "in" a room is a membership row, not
a running process.

We already have the human half — `topic_memberships` with roles and mentions —
and we already have the agent's identity, since `agent_bindings` makes 芝士 a
real user rather than a special case. What is missing is the definition of an
agent's *presence*: today presence is a live session, a live session is a
process, and a process can only be in one place.

> If a session is what it means to be present, an agent can only ever be in one
> room, and agents can only message each other point to point.

Claude Code is the proof. Its inter-agent messaging is now genuinely rich — named
addressing, resumable subagents, teams with a shared task list, direct access to
a teammate's transcript — and it still cannot broadcast, because there is no
place everyone is in. A commenter under the announcement asked for exactly the
missing piece: dedicated channels where multiple agents can discuss.

We have the place. `blocks` is a database table scoped to a topic, readable by
anyone, with a UI — an order of magnitude more durable than a per-session mailbox
file. **The room is the half we are ahead on. The actor is the half we lack.**

## Four layers, and what each one's lifetime is

The settled model:

```
房间          lives forever      messages, roster, membership
 └─ 一件事     ends when done     branch/PR, accept card, progress
     ├─ 会话    resumable          --resume; reopen when it outgrows itself
     └─ 工作区   disposable         rebuilt from the branch
```

Against today, where all four are 话题 and therefore share the task's lifetime.

Three separate things get confused here often enough to be worth naming
individually:

| | What it is | How many | Keyed by |
|---|---|---|---|
| Room messages | `blocks` rows | **one, shared** | room |
| Conversation | the context formed by reading them | one per reader | (room, who) |
| Sandbox | container and files | one per worker | (room, who) |

The message layer is the one we already got right: `blocks` has no relationship
to any container, so any number of readers can read it any number of times. That
is why multi-agent rooms are a smaller change than they look — only the lower two
layers are keyed wrongly.

Multiple agents in one room therefore share *the conversation*, not *the
workspace*. Sharing a worktree would guarantee collisions; buzz runs several
agents in a channel the same way, with per-session tool instances resolving
against their own working directory.

### Consequences

**Rooms stop disappearing.** `archived_at` changes meaning from "this room is
over" to "this piece of work is done". A room persists whether or not anything is
running in it.

**Rooms persisting forces the task layer to exist.** Once a room outlives its
first task, a second task needs somewhere to live, and it cannot share the first
one's branch. Git's branch semantics are per-change, so per-room branches stop
working the moment rooms are durable. buzz can get away without a task layer
because it never binds git to a channel; we cannot.

**Rooms persisting also forces sandboxes to be disposable.** If every durable
room held a container, container count would equal room count and never fall.
Release on idle, rebuild on the next message — then container count tracks
concurrent work instead. Which in turn requires that continuity not live in the
tmux session: the `agent_sessions` resume token, per-topic transcript persistence,
and `--resume` already exist and are currently used only for cloning a conversation
into another topic. Inverting that assumption is a smaller change than it appears.

**Anything that must outlive a turn has to leave the container.** Three exits:
git (branch/PR), the room (messages, artifacts, previews), and memory (the API).
Everything else in the container is scratch paper.

## Where the platform ends

One rule decides whether something is a platform concept or an implementation
detail:

> **Does its output land in a public record the room can see?**

| | Verdict |
|---|---|
| A task | Platform. Output goes to `blocks`; anyone in the room can read it and reply to steer it. |
| Subagents, teams, a helper process inside the container | Implementation. Output stays in that container's transcript; only its spawner sees it. |

The dividing line is not whether a human *can* intervene — Claude Code shows they
can, in all four of its forms. It is **who** the output is visible to.

## What a task looks like

Not an entry in the left-hand tree. A **card in the room's timeline** — a task
being created is itself an event in the room — which expands into a thread.

```
┌──────────────────────────────────────────┐
│ ⏳ Fix cookie loss after logout           │
│ 芝士-A · feat/fix-logout-cookie · PR #41  │
│ ✓ reproduce  ✓ locate  ⋯ patch proxy cfg  │
│                          [expand · accept] │
└──────────────────────────────────────────┘
```

Collapsed, the room sees one card quietly updating in place — the same move as
Claude Tag's checklist, which relies on Slack not notifying on edits. Expanded,
it is that task's own conversation.

This flattens the place axis without losing decomposition: the structure moves
from the navigation tree into the room's content. What is genuinely lost is
nesting — a task cannot be split into sub-tasks. Claude Code made the same call
(teammates cannot spawn teammates).

Cards must stay addressable — their own id and link, like a thread permalink. The
link's meaning changes from "an independent place" to "this card in this room",
which is what makes the three one-way mechanisms collapse into ordinary actions:
referencing a card is referencing a message, `cheese notify` is sending a message
to another room (no longer special once rooms are a flat set), and a conclusion is
a result posted when the card closes.

## Memory

Scoped to the project, one per agent — the same way people work.

An agent is not a cross-project entity; it exists inside a project. buzz agrees:
all agent state is community-local, and 项目 is our community. So the scope is
right today; only the key is wrong.

| | scope | scope_id |
|---|---|---|
| Today | `project` | project id |
| Add | `agent_project` | project id + the agent's account |

The difference only shows up when a project has more than one agent — which is
exactly the ops-agent case. With one pool, one agent's operational trivia dilutes
the other's product decisions. With a key per agent, they each keep their own,
and neither can take it to another project.

`memory_entries` is already `(scope, scope_id, content)` with `scope` as an enum,
and `recall`/`remember`/`search` already take `scope` as a parameter. This is a
new enum value, not a schema change — the only data-model change in this whole
document.

**There is no shared memory pool, and there should not be.** No team has a shared
brain; teams share documents. A pool everyone writes to is a pool nobody owns and
therefore nobody trusts. Public knowledge already has homes here: 活文档
(`cheese doc`), `cheese decision`, `cheese milestone`, and `CLAUDE.md`. Claude Tag
draws the same line — memory is one of five knowledge locations, and the guidance
is to keep playbooks in the repository rather than in memory.

The layer above the project is *configuration*, not memory: what this 芝士 is
like, as distinct from what it has learned. buzz separates these into two event
kinds for the same reason.

```
persona (global config)   what this 芝士 is like
 └ project
    ├ memory (account, project)   what it learned here
    └ documents (public)          what the project knows
```

## What is left

**Migration cost.** Real data and real habit sit on the existing tree. Worth
measuring before sequencing: how many topics have subtopics, how deep, and how
many project memory entries exist per project. Existing project memories can be
attributed to whoever wrote them; anything that is genuinely public knowledge
moves into 活文档.

**Not in this document:** schema, sequencing, or an implementation plan. The
model is settled enough to write one against; that is a separate document.
