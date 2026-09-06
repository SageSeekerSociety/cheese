import type { Team } from '@/types/teams'

import { ref } from 'vue'

// One 新建项目 dialog serves both entry points: the rail ＋ and a team page's
// 新建项目 button. The dialog itself lives in App.vue, where it outlives any
// route, so the team page opens it through this module-level state instead
// of owning a copy that would drift.
const open = ref(false)
const presetTeamId = ref<number | null>(null)

export function useNewProjectDialog() {
  function show(teamId: number | null = null) {
    presetTeamId.value = teamId
    open.value = true
  }
  return { open, presetTeamId, show }
}

/**
 * Which team the dialog offers first: the one the caller named, if it is still
 * among mine; else my personal team; else the first one there is.
 *
 * Before this dialog asked, a project made from the rail ＋ landed in the
 * personal team no matter which team page you had come from, and a project
 * made on the team page could not be named. Teammates then saw nothing, or
 * saw 「未命名项目」.
 */
export function defaultTeamFor(presetTeamId: number | null, teams: Team[]): number | null {
  if (presetTeamId !== null && teams.some((t) => t.id === presetTeamId)) return presetTeamId
  return teams.find((t) => t.personal)?.id ?? teams[0]?.id ?? null
}

/** The team a page belongs to, read off its path: `/teams/:teamId/...`. */
export function teamIdInPath(path: string): number | null {
  const m = /^\/teams\/(\d+)(?:\/|$)/.exec(path)
  return m ? Number(m[1]) : null
}
