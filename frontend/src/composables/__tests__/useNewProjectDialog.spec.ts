// 新建项目 asks which team the project belongs to, so a project made from a
// team page lands in that team and a project made from the rail ＋ does not
// silently land in the personal one.
import type { Team } from '@/types/teams'

import { describe, expect, it } from 'vitest'

import { defaultTeamFor, teamHandleInPath, useNewProjectDialog } from '../useNewProjectDialog'

const team = (id: number, personal = false) =>
  ({ id, personal, handle: `crew-${id}`, name: `t${id}` }) as unknown as Team

describe('defaultTeamFor', () => {
  it('offers the team the caller named when it is one of mine', () => {
    expect(defaultTeamFor(7, [team(1, true), team(7)])).toBe(7)
  })
  it('offers the team whose page I am on, named by its handle', () => {
    expect(defaultTeamFor('CREW-7', [team(1, true), team(7)])).toBe(7)
    expect(defaultTeamFor('crew-99', [team(1, true), team(7)])).toBe(1)
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

describe('teamHandleInPath', () => {
  it('reads the team off a team page', () => {
    expect(teamHandleInPath('/teams/zhishi')).toBe('zhishi')
    expect(teamHandleInPath('/teams/zhishi/members')).toBe('zhishi')
  })
  it('reads nothing off any other page', () => {
    expect(teamHandleInPath('/')).toBeNull()
    expect(teamHandleInPath('/projects/abc')).toBeNull()
  })
})

describe('useNewProjectDialog', () => {
  it('preserves a task and its team, then clears the task for ordinary creation', () => {
    const { open, sourceTask, presetTeam, show } = useNewProjectDialog()
    show(42, { id: 7, name: 'Research proposal' })
    expect(sourceTask.value).toEqual({ id: 7, name: 'Research proposal' })
    expect(presetTeam.value).toBe(42)
    open.value = false
    show(8)
    expect(sourceTask.value).toBeNull()
    expect(presetTeam.value).toBe(8)
    open.value = false
  })
  it('opens with the team the caller named', () => {
    const { open, presetTeam, show } = useNewProjectDialog()
    show(42)
    expect(open.value).toBe(true)
    expect(presetTeam.value).toBe(42)
    open.value = false
    show()
    expect(presetTeam.value).toBeNull()
  })
})
