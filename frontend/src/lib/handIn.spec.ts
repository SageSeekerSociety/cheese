import { describe, expect, it } from 'vitest'

import { acceptOffered, handInAction, noteHandedIn, noteSaved, savedRecently, submitsAndAdopts } from './handIn'

describe('what the task header offers a member who saved changes', () => {
  it('offers nothing to hand in when every change is in the latest version', () => {
    expect(handInAction({ unhanded: false, waiting: false })).toBeNull()
    expect(handInAction({ unhanded: false, waiting: true })).toBeNull()
  })

  it('offers submitting for review when nothing is waiting', () => {
    expect(handInAction({ unhanded: true, waiting: false })).toBe('submit')
  })

  it('offers another version when one is already waiting', () => {
    expect(handInAction({ unhanded: true, waiting: true })).toBe('resubmit')
  })

  it('does not offer to accept a waiting version that lacks the saved changes', () => {
    expect(acceptOffered({ unhanded: true, waiting: true })).toBe(false)
    expect(acceptOffered({ unhanded: false, waiting: true })).toBe(true)
  })

  it('submits and accepts in one go only when the person reviews it themselves', () => {
    expect(submitsAndAdopts('alice', 'alice')).toBe(true)
    expect(submitsAndAdopts('bob', 'alice')).toBe(false)
    expect(submitsAndAdopts(null, 'alice')).toBe(false)
  })

  it('counts a save as not handed in until it is handed in, for a while', () => {
    noteSaved('task-1', 1_000)
    expect(savedRecently('task-1', 2_000)).toBe(true)
    expect(savedRecently('task-2', 2_000)).toBe(false)
    expect(savedRecently('task-1', 1_000 + 120_000)).toBe(false)
    noteHandedIn('task-1')
    expect(savedRecently('task-1', 2_000)).toBe(false)
  })
})
