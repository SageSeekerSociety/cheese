// 新建项目 asks which team the project belongs to, so a project made from a
// team page lands in that team and a project made from the rail ＋ does not
// silently land in the personal one.
import type { Team } from '@/types/teams'

import { describe, expect, it } from 'vitest'

import { defaultTeamFor, teamIdInPath, useNewProjectDialog } from '../useNewProjectDialog'

const team = (id: number, personal = false) => ({ id, personal, name: `t${id}` }) as unknown as Team

describe('defaultTeamFor', () => {
  it('offers the team the caller named when it is one of mine', () => {
    expect(defaultTeamFor(7, [team(1, true), team(7)])).toBe(7)
  })
  it('falls back to my personal team when the named one is not mine', () => {
    expect(defaultTeamFor(99, [team(7), team(1, true)])).toBe(1)
  })
  it('falls back to the first team when there is no personal one', () => {
    expect(defaultTeamFor(null, [team(7), team(8)])).toBe(7)
  })
  it('offers nothing when I am on no team at all', () => {
    expect(defaultTeamFor(null, [])).toBeNull()
  })
})

describe('teamIdInPath', () => {
  it('reads the team off a team page', () => {
    expect(teamIdInPath('/teams/1499')).toBe(1499)
    expect(teamIdInPath('/teams/1499/members')).toBe(1499)
  })
  it('reads nothing off any other page', () => {
    expect(teamIdInPath('/')).toBeNull()
    expect(teamIdInPath('/projects/abc')).toBeNull()
    expect(teamIdInPath('/teams/mine')).toBeNull()
  })
})

describe('useNewProjectDialog', () => {
  it('preserves a task and its team, then clears the task for ordinary creation', () => {
    const { open, sourceTask, presetTeamId, show } = useNewProjectDialog()
    show(42, { id: 7, name: 'Research proposal' })
    expect(sourceTask.value).toEqual({ id: 7, name: 'Research proposal' })
    expect(presetTeamId.value).toBe(42)
    open.value = false
    show(8)
    expect(sourceTask.value).toBeNull()
    expect(presetTeamId.value).toBe(8)
    open.value = false
  })
  it('opens with the team the caller named', () => {
    const { open, presetTeamId, show } = useNewProjectDialog()
    show(42)
    expect(open.value).toBe(true)
    expect(presetTeamId.value).toBe(42)
    open.value = false
    show()
    expect(presetTeamId.value).toBeNull()
  })
})
