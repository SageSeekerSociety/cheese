# Session-record views: integration notes

The overview and raw view derive harness activity from `session_records`. The overview groups tool results and child records under their initiating calls. The raw view retains native identity, timestamps, thinking, complete arguments/output and expandable JSON. The browser remembers the selected view; overview is the default. Existing platform statuses remain in `run_records`, with every transition appended and the overview selecting the latest transition of the same platform status.

The shared reader merges hot database records and verified, permanently published bucket objects by native identity. Sealing uploads and checks the object before publishing its permanent index; only published objects become discoverable after row expiry. Pagination, literal search, author filtering, exact-record reads and channel-wide room/task/thread search use this path. Deleted channels immediately deny records and images. Turns before the launch boundary without native records receive one explanatory line.

Claude Code stores images separately by SHA-256, up to 10 MiB per image (displayed as 10 MB). Images exceeding that limit are omitted with their size retained. Mirror transport carries image sidecars before advancing the durable raw cursor; private-image reads enforce room membership. Runner JSON no longer trims text or strips image metadata before recording. Codex and pi harness-specific work, including the reasoning-summary setting and gateway probe, is outside this change.

## Removed runtime mechanism

Hidden tool, narration and subagent event-block writes and mutations are removed, along with the event transcript reader, step-output API, capped-output helper, old row components and clamp composable. Memory read counts and the agent CLI's event search now consume native session records. Deliberately published room messages and platform statuses remain. The room red light, liveness hints and transcript-derived control path belong to branch `feat/runner-lifecycle-protocol`.

## Data deletion: confirmation required before integration merges

No deployed data was changed. The read-only dev transaction reported `transaction_read_only=on` and these counts:

- Hidden event blocks matching `kind='event' AND meta->>'in_room'='false'`: 1,139,682.
- All event blocks: 1,168,297.
- Hidden tool rows: 1,005,193; hidden subagent rows: 6,897; hidden rows with output: 685,439. Hidden narration rows: 127,240. Categories overlap.
- Retired `run_records`: 0. Platform statuses still use this table.

The coordinator must confirm deletion with the product owner before merging. Release one removes readers/writers and adds only the conversation launch boundary. Release two removes the hidden event rows in bounded, committed periodic batches after the preceding release has stopped producing them. More than 10,000 rows must not be removed by a blocking migration. `blocks` and `run_records` are shared tables; no table or column is exclusive to the retired mechanism, so there is no schema drop to schedule. The count script is read-only and reproducible.

## Sources adopted

- [Claude Code interactive transcript](https://code.claude.com/docs/en/interactive-mode): progressive disclosure of execution details and thinking.
- [Codex rollout trace](https://github.com/openai/codex/blob/main/codex-rs/rollout-trace/README.md): append-only evidence, exact payloads and projections retaining raw identity.
- [LangSmith observability](https://docs.langchain.com/langsmith/observability-quickstart): inspect tool inputs/results within execution structure.
- [Langfuse data model](https://langfuse.com/docs/observability/data-model): distinct session, trace and observation identities, with nested observations.
- [OpenTelemetry GenAI attributes](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/registry/attributes/gen-ai.md): structured messages and provider tool-call IDs for correlation. The view joins calls/results through those identities.

Read through `lark-cli`: 《愿景：让好奇心拥有改变世界的力量》 and 《项目介绍-知是2.0-CheeseX》. Their emphasis on shared work and inspecting AI work informed the neutral copy. View names and defaults follow design issue #4398.

The two-page Chinese review PDF contains current local component screenshots of both views, explicitly marked as sample data and not deployed. Regression evidence and final validation results accompany the handoff report.
