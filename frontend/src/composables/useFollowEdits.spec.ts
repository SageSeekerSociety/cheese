/**
 * 跟随 follows the edit the AI teammate most recently wrote to, in the task's own path. A change
 * that has run says so (the file shown is read again), whether anyone follows or not; one that
 * failed changed nothing and is dropped; a late frame never takes back a change that has run.
 * Following starts when the person asks and stops when they act.
 */
import type { WsServerFrame } from '../cx_types'

import { effectScope } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { LANDED_MS, STALE_MS, useFollowEdits } from './useFollowEdits'

function live(id: string, args: string): WsServerFrame {
  return { type: 'live', turn_id: 't1', agent: 'cheese', blocks: [{ type: 'tool', id, name: 'Edit', arguments: args }] }
}
const landed = (id: string, failed = false): WsServerFrame => ({
  type: 'landed',
  agent: 'cheese',
  calls: [{ call_id: id, failed }],
})
const ARGS = '{"file_path":"/work/kernel/proc.c","old_string":"return -1;","new_string":"return pid;"}'

function setup() {
  const reread: string[] = []
  const scope = effectScope()
  const follow = scope.run(() =>
    useFollowEdits({ listed: () => ['kernel/proc.c', 'user/sh.c'], landed: (file) => reread.push(file) })
  )!
  return { follow, reread, scope }
}

afterEach(() => vi.useRealTimers())

describe('跟随', () => {
  it('follows the edit being written, in the task file it changes', () => {
    const { follow } = setup()
    follow.frame(live('e1', '{"file_path":"/work/kernel/proc.c","old_string":"return -1;","new_string":"ret'))
    expect(follow.current.value).toMatchObject({ file: 'kernel/proc.c', text: 'ret', state: 'writing' })
    expect(follow.active.value).toBe(true)
    follow.frame(live('e1', ARGS))
    expect(follow.current.value?.text).toBe('return pid;')
  })

  it('a change that ran has the file read again, followed or not', () => {
    const { follow, reread } = setup()
    follow.frame(live('e1', ARGS))
    follow.frame(landed('e1'))
    expect(follow.current.value?.state).toBe('landed')
    expect(follow.active.value).toBe(false)
    expect(reread).toEqual(['kernel/proc.c'])
  })

  it('a change that failed changed nothing: no read, nothing drawn', () => {
    const { follow, reread } = setup()
    follow.frame(live('e1', ARGS))
    follow.frame(landed('e1', true))
    expect(reread).toEqual([])
    expect(follow.current.value).toBeNull()
  })

  it('a late frame does not take back a change that ran', () => {
    const { follow } = setup()
    follow.frame(live('e1', ARGS))
    follow.frame(landed('e1'))
    follow.frame(live('e1', ARGS))
    expect(follow.current.value?.state).toBe('landed')
  })

  it('the newest edit is the one followed', () => {
    const { follow } = setup()
    follow.frame(live('e1', ARGS))
    follow.frame(live('e2', '{"file_path":"/work/user/sh.c","old_string":"x","new_string":"y"}'))
    expect(follow.current.value?.file).toBe('user/sh.c')
  })

  it('a landed change stops being marked after a while; an edit that never ran is dropped later', () => {
    vi.useFakeTimers()
    const { follow } = setup()
    follow.frame(live('e1', ARGS))
    follow.frame(landed('e1'))
    vi.advanceTimersByTime(LANDED_MS + 10)
    expect(follow.current.value).toBeNull()

    follow.frame(live('e2', ARGS))
    vi.advanceTimersByTime(STALE_MS - 1000)
    expect(follow.current.value?.call).toBe('e2')
    vi.advanceTimersByTime(2000)
    expect(follow.current.value).toBeNull()
  })

  it('following is the person’s to start; another task starts with nothing', () => {
    const { follow } = setup()
    expect(follow.following.value).toBe(false)
    follow.start()
    follow.frame(live('e1', ARGS))
    follow.reset()
    expect(follow.following.value).toBe(false)
    expect(follow.current.value).toBeNull()
  })
})
