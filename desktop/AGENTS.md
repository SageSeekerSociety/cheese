# desktop/ — native-shell verification

Read [`../AGENTS.md`](../AGENTS.md) first.

Distinguish the web page from the installed native shell. A site deployment
updates the page, but a change under `src-tauri/` requires a newly built app;
reloading an old install cannot verify it. `.github/workflows/desktop.yml`
builds architecture-specific DMG artifacts for desktop PRs, which can be used
for isolated pre-merge verification; that is not a production deployment.

For selection, dragging and context-menu failures, also inspect
`frontend/src/lib/desktopNative.ts`. Its guards are absent in a plain browser,
and the context-menu guard is omitted in dev builds, so a browser-only or
`tauri dev` check does not cover every installed-app interaction.
