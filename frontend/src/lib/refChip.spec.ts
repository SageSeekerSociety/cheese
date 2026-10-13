// A reference chip (@person, #topic, a file) opens something when clicked, so a
// keyboard user has to be able to reach it and press it. Inside a document being
// edited it stays out of the Tab order: there the caret walks the text.
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

import { installRefChipKeys, refChip } from './refChip'
import { renderPlain } from './renderMessage'

const names = { mentionNames: { alice: 'Alice' }, topicTitles: {} }

beforeAll(() => installRefChipKeys())
afterEach(() => document.body.replaceChildren())

/** A message drawn the way chat draws it, with a click delegated on the list around it. */
function message(text: string) {
  const list = document.createElement('div')
  list.innerHTML = renderPlain(text, names)
  const opened = vi.fn()
  list.addEventListener('click', (e) => {
    const chip = (e.target as HTMLElement).closest<HTMLElement>('.mention')
    if (chip) opened(chip.dataset.handle ?? chip.dataset.file)
  })
  document.body.append(list)
  return { chip: list.querySelector<HTMLElement>('.mention')!, opened }
}

describe('a reference chip in a message', () => {
  it('takes Tab focus and reads as a button', () => {
    const { chip } = message('ask <@alice> about it')
    expect(chip.getAttribute('role')).toBe('button')
    chip.focus()
    expect(document.activeElement).toBe(chip)
  })

  it.each(['Enter', ' '])('opens what it points at when %j is pressed on it', (key) => {
    const { chip, opened } = message('see <&src/a.ts>')
    chip.focus()
    chip.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }))
    expect(opened).toHaveBeenCalledWith('src/a.ts')
  })
})

describe('a reference chip in a document being edited', () => {
  it('is not a Tab stop', () => {
    const chip = refChip('@', 'alice', names, { focusable: false })
    // A span is in the Tab order only when it carries a tabindex.
    expect(chip.hasAttribute('tabindex')).toBe(false)
    expect(chip.getAttribute('role')).toBeNull()
  })
})
