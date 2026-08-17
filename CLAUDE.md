# Project Conventions

**This file states principles. It never states how things currently are.**

A fact about the present is a trap on a delay: the moment someone fixes what you described, your sentence is still sitting here, still reading as current, and the next agent believes it. Anything that would become wrong *because somebody did good work* belongs in `docs/` or in an issue — not in the file that loads on every single turn. The same goes for anything a tool already enforces: a linter that goes red teaches the rule better than a paragraph, and it cannot fall out of date.

| This kind of thing | Lives in |
|---|---|
| A principle you must hold to while changing code, that **no tool will stop you from violating** | this file |
| A design still being argued | issues |
| How things currently are, and the long explanations | `docs/` |
| A pitfall that only applies while touching one area | `.claude/rules/` |
| Anything equally true of **any** repo we host | the platform skill |

## This repo does not adapt to the platform

Cheese hosts other people's repositories, and **a repository must never have to change in order to be hosted** — no config file, no wrapper script, no paragraph in its CLAUDE.md explaining our sandbox. Every line a repo is asked to add is a reason not to adopt us, and for the repos we do not control, asking is not on the table.

So this file describes **this project** and nothing else. Anything equally true of a hosted repo goes in `backend/sandbox/skills/cheese/SKILL.md`, which reaches all of them at once; the same words here would fix it for us alone, and demonstrate the very adaptation we promise nobody has to make.

We are the easiest repo in the world to get this wrong in, being both the platform and one of the repos it hosts — so `check-repo-rules.sh` guards it. The tell, when you are unsure: **a rule that is false when you are NOT in a sandbox is leaked platform knowledge.**

## Read the issues before the docs, and clean the docs when a design lands

**The design that is still being argued lives in issues.** Whatever `docs/` says about it was written before the argument finished, so starting there means implementing something already superseded — and nothing in the file will tell you.

**Then, when a design does land, delete every sentence it invalidated — in the same PR.** A stale sentence reads exactly like a current one; that is the whole danger. A follow-up is a promise while the trap stays live, and annotating a section "outdated" leaves the trap in place while helping only whoever reads that far. Delete the claim; git history keeps it. Then close the issue with the reasoning, so the next agent finds a conclusion instead of re-arguing it.

## We are still building this — do not preserve what is already decided against

Once a decision is made to change something, change it. Do not leave a guard for
the shape being replaced, a note explaining when that guard expires, or a
compatibility path for a state nobody agreed to keep. Those are what a mature
system pays to protect the users it already has; paying early buys nothing and
leaves more to unwind.

Before defending any guard, check what it actually watches. One written after an
incident tends to point at where that incident *showed up*, and the code that
causes it moves; a guard can spend years watching the site of the last outage
rather than the entrance to the next one.

## Assume other agents are working right now

Not hypothetically — concurrently, in this repo, on adjacent files. Before starting a fix, look for someone already fixing it in an open PR. Before handing work off, review **every path your change touches**; a cache directory in that list is a stop sign.

## Test behaviour, never the implementation

Write functional tests. Do not read the source to work out what to assert — a test derived from the implementation passes by construction and proves nothing, including after the implementation breaks.

## Report real bugs, not theoretical ones

When auditing, a finding needs a way to actually happen: crash, corruption, security hole, wrong result. Style opinions and "this could in principle" are noise that buries the real ones.
