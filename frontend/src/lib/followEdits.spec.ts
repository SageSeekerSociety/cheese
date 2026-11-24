/**
 * An edit the AI teammate is writing is read off its live tool call as soon as it names its
 * file, and grows as the arguments do; any other tool says nothing. A path from inside the
 * environment is the task file it ends with.
 */
import type { LiveBlock } from '../types/live'

import { describe, expect, it } from 'vitest'

import { checkoutPath, editOf } from './followEdits'

const edit = (args: string, name = 'Edit'): LiveBlock => ({ type: 'tool', id: 'e1', name, arguments: args })

describe('an edit being written', () => {
  it('says nothing until its file is named whole', () => {
    expect(editOf(edit('{"file_path":"/work/ke'), 'cheese')).toBeNull()
  })

  it('knows where it goes once the replaced passage is whole, and grows its new text', () => {
    const half = editOf(edit('{"file_path":"/work/proc.c","old_string":"return -'), 'cheese')
    expect(half).toMatchObject({ path: '/work/proc.c', old: null, oldPending: true, text: '' })

    const writing = editOf(
      edit('{"file_path":"/work/proc.c","old_string":"return -1;","new_string":"return p'),
      'cheese'
    )
    expect(writing).toMatchObject({ old: 'return -1;', oldPending: false, text: 'return p', state: 'writing' })
  })

  it('a write of a whole file replaces all of it', () => {
    const written = editOf(edit('{"file_path":"/work/notes.md","content":"# Lab 2\\n', 'Write'), 'cheese')
    expect(written).toMatchObject({ old: null, oldPending: false, text: '# Lab 2\n' })
  })

  it('reads the same tool behind a harness prefix, and nothing from other tools', () => {
    expect(editOf(edit('{"file_path":"/work/a.c"}', 'mcp__files__Edit'), 'cheese')).not.toBeNull()
    expect(editOf(edit('{"command":"make"}', 'Bash'), 'cheese')).toBeNull()
    expect(editOf({ type: 'text', text: 'Fixing fork.' }, 'cheese')).toBeNull()
  })
})

describe('the task file a path names', () => {
  const listed = ['kernel/proc.c', 'proc.c', 'user/sh.c']

  it('is the longest listed path it ends with', () => {
    expect(checkoutPath('/home/agent/work/kernel/proc.c', listed)).toBe('kernel/proc.c')
    expect(checkoutPath('user/sh.c', listed)).toBe('user/sh.c')
  })

  it('is none outside the checkout', () => {
    expect(checkoutPath('/tmp/scratch.c', listed)).toBeNull()
    expect(checkoutPath('/work/xproc.c', ['proc.c'])).toBeNull()
  })
})
