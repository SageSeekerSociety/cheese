"""The pages the open-time probe walks, and what "this page has drawn" means.

Each row pairs a route with the selector that only appears once the page's own
data is on screen — not the shell around it, and not a skeleton. `min_text` is
how much text that selector has to hold before the frame counts, which keeps an
empty container (rendered immediately, filled later) from being mistaken for a
finished page. `not_ready` is a selector that is still on screen while loading;
when it matches, the page is not ready no matter what else is there.

Route templates use {placeholders} filled from params.json — the ids of the
seeded demo projects, channels, tasks and threads, so the pages have something
to draw. See README.md.

Where a selector was read straight out of the view component it is followed by
the file it came from, so the next person can re-check it when a page is
redesigned.
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
    note: str = ""

    def route_path(self, params: dict[str, object]) -> str:
        """The app-internal path — the form the router is pushed with."""
        return self.route.format(**params)

    def path(self, base_url: str, params: dict[str, object]) -> str:
        """The full URL, for loading the page from scratch (cold mode)."""
        return base_url + self.route_path(params)


# The skeleton the whole app shares, plus the two page-level busy shapes. Any of
# them on screen means "still loading".
SKELETON = ".skel"
SPINNER = ".v-progress-circular"
REVEAL_GATE = ".reveal-gate--waiting"

PAGES: list[PageSpec] = [
    # --- pages that fetch their own data on mount -------------------------
    PageSpec(
        "inbox",
        "/inbox",
        ".inbox__list",
        min_text=8,
        not_ready_selector=".inbox__quiet " + SPINNER,
        note="views/InboxView.vue",
    ),
    PageSpec(
        "settings-devices",
        "/users/settings/devices",
        ".device",
        min_text=4,
        not_ready_selector=SPINNER,
        note="views/MyDevicesView.vue",
    ),
    PageSpec(
        "settings-connections",
        "/users/settings/connections",
        ".conn-row",
        min_text=4,
        note="views/MyConnectionsView.vue",
    ),
    PageSpec(
        "settings-notifications",
        "/users/settings/notifications",
        ".notif-matrix",
        min_text=4,
        not_ready_selector=SPINNER,
        note="views/user/settings/Notifications.vue",
    ),
    PageSpec(
        "user-profile",
        "/users/{handle}",
        ".profile__name",
        min_text=1,
        not_ready_selector='[aria-busy="true"]',
        note="views/ProfileView.vue",
    ),
    PageSpec(
        "project-channels",
        "/projects/{projectId}/channels",
        ".browse__row",
        min_text=3,
        not_ready_selector="[role='status']",
        note="views/workspace/ChannelBrowse.vue",
    ),
    PageSpec(
        "project-tasks",
        "/projects/{projectId}/tasks",
        ".tasks__row",
        min_text=3,
        not_ready_selector=SKELETON,
        note="views/workspace/ProjectTasks.vue",
    ),
    PageSpec(
        "project-library",
        "/projects/{projectId}/library",
        ".library__list",
        min_text=8,
        not_ready_selector=SKELETON,
        note="views/ProjectLibraryView.vue",
    ),
    PageSpec(
        "project-docs-charter",
        "/projects/{projectId}/docs/charter",
        ".docs-tabs",
        min_text=1,
        not_ready_selector=SPINNER,
        note="views/ProjectDocsView.vue",
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
        not_ready_selector=SKELETON + ", " + REVEAL_GATE,
        note="views/workspace/ProjectMembersView.vue",
    ),
    PageSpec(
        "project-settings",
        "/projects/{projectId}/settings",
        ".settings-page-body",
        min_text=8,
        not_ready_selector=REVEAL_GATE,
        note="views/ProjectSettingsView.vue",
    ),
    PageSpec(
        "project-skills",
        "/projects/{projectId}/skills",
        ".skills-row",
        min_text=3,
        not_ready_selector=SKELETON,
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
    PageSpec(
        "spaces",
        "/spaces",
        ".space-card",
        min_text=1,
        not_ready_selector=SKELETON,
        note="views/spaces/Index.vue",
    ),
    # --- the core pages, as a control group -------------------------------
    PageSpec(
        "channel",
        "/projects/{projectId}/channels/{topicId}",
        ".im-row[data-mid]",
        min_text=1,
        not_ready_selector=".skel--chat",
        note="views/workspace/TopicView.vue — a channel's own conversation",
    ),
    PageSpec(
        "thread",
        "/projects/{projectId}/channels/{topicId}/threads/{threadId}",
        '[data-testid="thread-pane"] .thread-root',
        min_text=1,
        note="views/workspace/ThreadPane.vue — the channel column is already up, "
        "so the root reply is what proves the thread itself arrived",
    ),
    PageSpec(
        "workspace-task",
        "/projects/{projectId}/tasks/{taskId}",
        ".task-header__title",
        min_text=1,
        not_ready_selector=".skel--chat",
        note="views/workspace/TopicView.vue + components/task/TaskHeader.vue",
    ),
]
