import type { RoomTask } from '@/cx_types'

import { describe, expect, it } from 'vitest'

import { RAIL_TASKS, railTasksByChannel } from './railTasks'

let n = 0
function task(over: Partial<RoomTask>): RoomTask {
  n += 1
  return {
    id: `t${n}`,
    project_id: 'p',
    room_id: 'general',
    title: `task ${n}`,
    status: 'open',
    created_at: '2026-10-01T00:00:00Z',
    updated_at: '2026-10-01T00:00:00Z',
    presentation: { column: 'building', phrase: 'started' },
    ...over,
  }
}

const at = (day: number) => `2026-10-${String(day).padStart(2, '0')}T00:00:00Z`

describe('railTasksByChannel', () => {
  it('lists what I own before what I help with, each most recently active first', () => {
    const helpingNew = task({ owner_handle: 'bob', contributor_handles: ['me'], last_activity_at: at(9) })
    const mineOld = task({ owner_handle: 'me', last_activity_at: at(2) })
    const mineNew = task({ owner_handle: 'me', last_activity_at: at(5) })

    const { shown } = railTasksByChannel([helpingNew, mineOld, mineNew], 'me').general

    expect(shown.map((t) => t.id)).toEqual([mineNew.id, mineOld.id, helpingNew.id])
  })

  it('shows at most a handful and counts every open task in the channel', () => {
    const mine = Array.from({ length: RAIL_TASKS + 2 }, (_, i) =>
      task({ owner_handle: 'me', last_activity_at: at(i + 1) })
    )
    const helping = task({ owner_handle: 'bob', contributor_handles: ['me'], last_activity_at: at(20) })
    const others = task({ owner_handle: 'bob' })

    const channel = railTasksByChannel([...mine, helping, others], 'me').general

    expect(channel.shown).toHaveLength(RAIL_TASKS)
    expect(channel.shown).not.toContain(helping)
    expect(channel.total).toBe(mine.length + 2)
  })

  it("leaves out other people's tasks and finished ones", () => {
    const others = task({ owner_handle: 'bob' })
    const closed = task({ owner_handle: 'me', status: 'closed' })
    const done = task({ owner_handle: 'me', presentation: { column: 'done', phrase: 'accepted' } })

    const channel = railTasksByChannel([others, closed, done], 'me').general

    expect(channel.shown).toEqual([])
    expect(channel.total).toBe(1)
  })
})
