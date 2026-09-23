import type { Team } from '@/types/teams'

import { ref } from 'vue'

// One 新建项目 dialog serves both entry points: the rail ＋ and a team page's
// 新建项目 button. The dialog itself lives in App.vue, where it outlives any
// route, so the team page opens it through this module-level state instead
// of owning a copy that would drift.
const open = ref(false)
// The team to offer first: its id from a team's own page, or its handle when all
// the caller has is the address it is standing on.
const presetTeam = ref<number | string | null>(null)
const sourceTask = ref<{ id: number; name: string } | null>(null)

export function useNewProjectDialog() {
  function show(team: number | string | null = null, task: { id: number; name: string } | null = null) {
    presetTeam.value = team
    sourceTask.value = task
    open.value = true
  }
  return { open, presetTeam, sourceTask, show }
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
export function defaultTeamFor(preset: number | string | null, teams: Team[]): number | null {
  const named =
    typeof preset === 'string'
      ? teams.find((t) => t.handle.toLowerCase() === preset.toLowerCase())
      : teams.find((t) => t.id === preset)
  if (named) return named.id
  return teams.find((t) => t.personal)?.id ?? teams[0]?.id ?? null
}

/** The team a page belongs to, read off its path: `/teams/:handle/...`. */
export function teamHandleInPath(path: string): string | null {
  const m = /^\/teams\/([A-Za-z0-9_-]+)(?:\/|$)/.exec(path)
  return m ? decodeURIComponent(m[1]) : null
}
