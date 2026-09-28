# Project compute configurations

**Goal:** Rooms work with the default cloud configuration, project managers set the default, and room selections never change project defaults.

**Architecture:** Project settings hold an explicit default. A room stores the configuration its agents start on. Once an agent session starts working it keeps its own machine; the room's configuration stays editable as the default for agents that start later. Team device registration remains the sharing boundary. Cloud execution accepts authenticated team members within the existing team quota; manual machine administration retains its existing authority checks.

**Implementation:**

1. Add typed compute choices, project configuration endpoints, and a nullable room configuration snapshot. Validate project management and team/device scope. Support the currently connected cloud's CPU, memory and disk configuration; do not invent GPU or additional cloud offerings.
2. Remove room-to-project sticky writes. Resolve the same configuration in the picker and execution path, including a project's named device default. Pass requested cloud resources to provisioning and reject unsupported sizes rather than silently substituting them.
3. Show the project default first with a badge, followed by the room's current choice. Put other cloud configurations and team devices behind an expandable entry. Add project configuration management in project settings.
4. Verify temporary selection isolation, project management authorization, team device inheritance, resource sizing, member cloud access and started-room behavior. Run backend and frontend checks, then capture the changed components and deliver a user-language review PDF.

**Completion:** The scenarios above pass, changed UI renders in both themes, and the PR includes the implementation with a separate PDF artifact for review. No cloud purchase, production deployment, or new provider integration is part of this change.
