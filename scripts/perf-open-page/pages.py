"""The pages and core user operations the timing probe measures, and what "ready" means.

Each row pairs a route (or interactive action) with:
* `ready_selector`: selector that only matches once the target page's own data is
  rendered on screen — not the outer shell, not a loading skeleton, and not stale
  DOM left over from the previous route.
* `min_text`: minimum visible character count inside `ready_selector` so an empty
  container mounted before data arrives does not count as ready.
* `not_ready_selector`: loading skeletons, spinners, or reveal gates; while any
  of them is visible, the page is not ready.
* `action_selector`: a primary interactive control on the page (e.g. the message
  composer on a channel or task, or the tab bar on project docs) that must be
  visible and not disabled before the page counts as usable.
* `empty_selector`: selector for the page's legitimate empty state, so the probe
  distinguishes "content rendered" from "empty state rendered" instead of
  hanging until timeout or misreporting an empty state as full content.

Route templates use {placeholders} filled from params.json — the ids of the
seeded demo projects, channels, tasks, documents and threads. See README.md.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PageSpec:
    name: str
    route: str
    ready_selector: str
    min_text: int = 1
    not_ready_selector: str | None = None
    action_selector: str | None = None
    empty_selector: str | None = None
    allow_empty: bool = False
    kind: str = "page"
    note: str = ""

    def route_path(self, params: dict[str, object]) -> str:
        """The app-internal path — the form the router is pushed with."""
        return self.route.format(**params)

    def path(self, base_url: str, params: dict[str, object]) -> str:
        """The full URL, for loading the page from scratch (cold/warm mode)."""
        return base_url.rstrip("/") + self.route_path(params)


# The skeleton the whole app shares, plus the two page-level busy shapes. Any of
# them visible on screen means "still loading".
SKELETON = ".skel"
SPINNER = ".v-progress-circular"
REVEAL_GATE = ".reveal-gate--waiting"
BUSY = '[aria-busy="true"]'

PAGES: list[PageSpec] = [
    # --- pages that fetch their own data on mount -------------------------
    PageSpec(
        "inbox",
        "/inbox",
        ".waiting__section, .inbox-row",
        min_text=4,
        not_ready_selector=f".waiting__skel, {SKELETON}, {SPINNER}",
        empty_selector=".waiting__quiet",
        note="views/InboxView.vue + components/common/Notification/WaitingList.vue",
    ),
    PageSpec(
        "settings-devices",
        "/users/settings/compute",
        ".compute-row, .settings-page__head",
        min_text=2,
        not_ready_selector=SPINNER,
        empty_selector=".settings-page",
        note="views/teams/detail/Compute.vue + ComputeView.vue",
    ),
    PageSpec(
        "settings-connections",
        "/users/settings/connections",
        ".conn-row",
        min_text=4,
        not_ready_selector=SPINNER,
        empty_selector=".settings-empty",
        note="views/MyConnectionsView.vue",
    ),
    PageSpec(
        "settings-notifications",
        "/users/settings/notifications",
        ".notif-matrix",
        min_text=4,
        not_ready_selector=SPINNER,
        action_selector=".notif-dot",
        note="views/user/settings/Notifications.vue",
    ),
    PageSpec(
        "user-profile",
        "/users/{handle}",
        ".profile__name",
        min_text=1,
        not_ready_selector=f"{BUSY}, {SKELETON}",
        note="views/ProfileView.vue",
    ),
    PageSpec(
        "project-channels",
        "/projects/{projectId}/channels",
        ".browse__row",
        min_text=3,
        not_ready_selector=f"[role='status'], {SPINNER}, {SKELETON}",
        note="views/workspace/ChannelBrowse.vue",
    ),
    PageSpec(
        "project-tasks",
        "/projects/{projectId}/tasks",
        ".tasks__row",
        min_text=3,
        not_ready_selector=f"{SKELETON}, {SPINNER}",
        empty_selector=".tasks__empty",
        note="views/workspace/ProjectTasks.vue",
    ),
    PageSpec(
        "project-library",
        "/projects/{projectId}/library",
        ".library__list",
        min_text=8,
        not_ready_selector=f"{SKELETON}, .library__list [role='status']",
        action_selector=".library__search input",
        empty_selector=".library__empty",
        note="views/ProjectLibraryView.vue",
    ),
    PageSpec(
        "project-docs-charter",
        "/projects/{projectId}/docs/charter",
        ".doc-prose",
        min_text=4,
        not_ready_selector=f"{SPINNER}, {SKELETON}, .doc-skel",
        action_selector=".docs-tabs [role='tab']",
        empty_selector=".docs-tabs",
        note="views/ProjectDocsView.vue + components/panels/doc/DocSurface.vue",
    ),
    PageSpec(
        "project-document",
        "/projects/{projectId}/docs/{docId}",
        ".doc-prose",
        min_text=4,
        not_ready_selector=f"{SPINNER}, {SKELETON}, .doc-skel",
        action_selector=".doc-page__title--input, .doc-top",
        note="views/ProjectLibraryView.vue + components/library/LibraryDocumentPage.vue",
    ),
    PageSpec(
        "project-artifact",
        "/projects/{projectId}/artifacts/{artifactId}",
        ".artifact__facts, .artifact__main",
        min_text=4,
        not_ready_selector=SPINNER,
        note="views/ProjectArtifactView.vue",
    ),
    PageSpec(
        "project-members",
        "/projects/{projectId}/members",
        ".member-row",
        min_text=2,
        not_ready_selector=f"{SKELETON}, {REVEAL_GATE}",
        note="views/workspace/ProjectMembersView.vue",
    ),
    PageSpec(
        "project-settings",
        "/projects/{projectId}/settings",
        ".settings-page-body",
        min_text=8,
        not_ready_selector=f"{REVEAL_GATE}, {SPINNER}",
        note="views/ProjectSettingsView.vue",
    ),
    PageSpec(
        "project-skills",
        "/projects/{projectId}/skills",
        ".skills-row",
        min_text=3,
        not_ready_selector=f"{SKELETON}, [role='status']",
        note="views/ProjectSkillsView.vue",
    ),
    PageSpec(
        "teams",
        "/teams",
        ".my-team-item",
        min_text=3,
        not_ready_selector=SPINNER,
        note="views/teams/Mine.vue",
    ),
    PageSpec(
        "team-detail",
        "/teams/{teamHandle}",
        ".project-card",
        min_text=1,
        not_ready_selector=SPINNER,
        note="views/teams/Detail.vue + TeamProjects",
    ),
    # --- the core workspace pages and interactive operations --------------
    PageSpec(
        "channel",
        "/projects/{projectId}/channels/{topicId}",
        ".im-row[data-row-id], .im-row[data-mid]",
        min_text=4,
        not_ready_selector=f".skel--chat, {SKELETON}",
        action_selector=".composer-editor__input[role='textbox']",
        empty_selector=".chat-start, [data-chat-feed]:not([aria-busy='true']):not(:has(.im-row))",
        note="views/workspace/TopicView.vue — a channel's own conversation and usable composer",
    ),
    PageSpec(
        "thread",
        "/projects/{projectId}/channels/{topicId}/threads/{threadId}",
        '[data-testid="thread-pane"] .thread-root',
        min_text=1,
        not_ready_selector=f".skel--chat, {SKELETON}",
        note="views/workspace/ThreadPane.vue — the channel column is already up, "
        "so the root reply is what proves the thread itself arrived",
    ),
    PageSpec(
        "workspace-task",
        "/projects/{projectId}/tasks/{taskId}",
        ".task-header__title",
        min_text=2,
        not_ready_selector=f".skel--chat, .task-state {SPINNER}, {SKELETON}",
        action_selector=".composer-editor__input[role='textbox']",
        empty_selector=".chat-start",
        note="views/workspace/TopicView.vue + components/task/TaskHeader.vue",
    ),
    PageSpec(
        "send-message",
        "/projects/{projectId}/channels/{topicId}",
        ".im-row[data-row-id], .im-row[data-mid]",
        min_text=4,
        not_ready_selector=f".skel--chat, {SKELETON}",
        action_selector=".composer-editor__input[role='textbox']",
        kind="action:send_message",
        note="Type a message in the channel composer, send it, and measure local feedback + server ack",
    ),
]
