// A reference chip (@person, #channel, a file) opens something when clicked, so a
// keyboard user has to be able to reach it and press it. Inside a document being
// edited it stays out of the Tab order: there the caret walks the text.
//
// A `<#id>` names a channel, a task or a 支线. One that names none of them says
// so: it never shows a borrowed name and never opens a channel that is not there.
import type { ConversationRef } from '../types/conversationRef'
import type { RefNames } from './refChip'

import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

import { t } from '../i18n'

import { installRefChipKeys, refChip } from './refChip'
import { answerRefsWith, refRoute } from './refDirectory'
import { renderPlain } from './renderMessage'

const ROOM = '11111111-1111-4111-8111-111111111111'
const THREAD = '22222222-2222-4222-8222-222222222222'
const GONE = '33333333-3333-4333-8333-333333333333'

const resolveRefs = vi.fn(
  async (ids: string[]): Promise<Record<string, ConversationRef>> =>
    ids.includes(THREAD) ? { [THREAD]: { kind: 'thread', title: '截图在这里', room_id: ROOM, project_id: 'p1' } } : {}
)
answerRefsWith(resolveRefs)

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
    if (chip) opened(chip.dataset.handle ?? chip.dataset.file ?? chip.dataset.topic)
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

/** The chip once what it names has been asked about. */
async function settled(id: string) {
  refChip('#', id, names)
  await vi.waitFor(() => expect(resolveRefs).toHaveBeenCalledWith(expect.arrayContaining([id])))
  await new Promise((done) => setTimeout(done))
  return message(`见 <#${id}>`)
}

describe('a reference to what is not there', () => {
  it('says so instead of naming a channel, and opens nothing', async () => {
    const { chip, opened } = await settled(GONE)
    expect(chip.textContent).toBe(`#${t('work.room.chat.refMissing')}`)
    expect(chip.getAttribute('role')).toBeNull()
    chip.click()
    expect(opened).toHaveBeenCalledWith(undefined)
  })

  it('is the same for a token that is no id at all', () => {
    const { chip } = message('see <#frontend>')
    expect(chip.textContent).toBe(`#${t('work.room.chat.refMissing')}`)
    expect(chip.dataset.topic).toBeUndefined()
  })
})

describe('a reference to a 支线', () => {
  it('reads as that 支线 and opens it in its channel', async () => {
    const { chip, opened } = await settled(THREAD)
    expect(chip.textContent).toContain('截图在这里')
    chip.click()
    expect(opened).toHaveBeenCalledWith(THREAD)
    expect(refRoute('p1', THREAD)).toEqual({
      name: 'workspace-thread',
      params: { projectId: 'p1', topicId: ROOM, threadId: THREAD },
    })
  })
})

describe('a `@` chip in a message body', () => {
  // 「谁是谁」不从 @ 文本里猜：handle→脸的那份表由上游（名册）填好传进来，见 RefNames。
  const withFaces: RefNames = {
    mentionNames: { alice: 'Alice', 'cheese-t1': '芝士' },
    topicTitles: {},
    mentionIdentities: {
      alice: { avatar: null, agent: false },
      'cheese-t1': { avatar: null, agent: true },
    },
  }

  it('draws a person the same way the roster does, and keeps the text to just @name', () => {
    const chip = refChip('@', 'alice', withFaces)
    expect(chip.querySelector('.identity-face--person')).not.toBeNull()
    // 脸是装饰：chip 的文字仍然只是「@Alice」，复制正文、读屏都干净。
    expect(chip.textContent).toBe('@Alice')
    expect(chip.dataset.handle).toBe('alice')
  })

  it('draws a teammate as the superellipse', () => {
    const chip = refChip('@', 'cheese-t1', withFaces)
    expect(chip.querySelector('.identity-face--agent')).not.toBeNull()
    expect(chip.textContent).toBe('@芝士')
  })

  it('still draws a face when the roster has none for them — the chip is never bare text', () => {
    const chip = refChip('@', 'alice', names)
    expect(chip.querySelector('.identity-face--person')).not.toBeNull()
    expect(chip.textContent).toBe('@Alice')
  })

  it('falls back to the handle naming rule for a teammate the roster did not name', () => {
    const chip = refChip('@', 'cheese-t9', names)
    expect(chip.querySelector('.identity-face--agent')).not.toBeNull()
    expect(chip.textContent).toBe('@cheese-t9')
  })

  it('leaves the broadcast handles without a face — they are not a person', () => {
    const broadcast: RefNames = { mentionNames: { all: '所有人', here: '在线成员' }, topicTitles: {} }
    const chip = refChip('@', 'all', broadcast)
    expect(chip.querySelector('.identity-face')).toBeNull()
    expect(chip.textContent).toBe('@所有人')
  })
})
