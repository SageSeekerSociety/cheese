# Student project flow

The shared product owns teams, projects, conversations, work items, documents and deliverables. Institution-specific branding, identity providers, learning catalogs and admission rules belong to deployment configuration or business entry points. A competition creates or links an ordinary team project; it must not create a second collaboration model.

## First fixes

1. Extend `useNewProjectDialog`, `App.vue` and the task overview to reuse the existing team selector when creating a project from a task. Preserve the task association and proposed name. Show team-loading failures and prevent creation before a team is selected. Verify the task context survives creation and is cleared on the next ordinary creation.
2. Add starter prompts to the empty `ChatPanel`. A click fills an editable draft and focuses the composer; only sending submits it. Keep ordinary conversations and existing drafts intact. Verify empty, populated, archived and draft states.
3. Use the existing sanitized Markdown renderer for AI replies, briefs and conclusions in `PanelCard`. Keep human messages literal. Verify links, formatting and script removal.

## Registration and working records

- Preserve the document guard. Browser verification found that numbered lists serialized their sublists with two spaces, changing their nesting. Use the numbered marker's content column for indentation and tolerate spacing between adjacent list items; keep paragraph breaks, code and list depth strict.
- Treat contribution counts as activity, not credit. Link evidence of requests, edits and acceptance rather than inventing a score.
- On application, create or reuse an ordinary project owned by the participating team and open it immediately. Seed its document with the task introduction and a link to the complete requirements. Existing team access controls govern collaboration.
- Pending applications get a workspace; approval releases the competition's resource grant. Repeated approval must not issue the same grant again.
- Each document edit records the actor, human/AI identity, version and text diff. Unchanged document nodes retain their author; changed nodes receive the current editor's identity. Show the diff next to the edit event without folding it into an AI turn summary.

## Verification

Run the affected frontend tests, lint and type checks, and backend registration, protocol and document regressions. Inspect desktop and narrow layouts through web-plane. Record the real local application using synthetic student accounts: apply as a team, enter the generated workspace, request AI research and a draft, edit as another teammate, inspect work records, and reload to check persistence. The video must identify any unverified steps; a local student login does not prove fresh institutional SSO.

Produce a local PDF with actual revised views and remaining design decisions. Submit the fixes in a PR. The approved scope prioritizes this Cheese demonstration; it excludes the legacy portal and production data changes.
