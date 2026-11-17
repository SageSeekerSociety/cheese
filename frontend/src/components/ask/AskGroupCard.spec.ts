import type { Block } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { describe, expect, it } from 'vitest'

import AskGroupCard from './AskGroupCard.vue'

import i18n, { setLocale } from '@/i18n'

function question(id: string, meta: Record<string, unknown> = {}): Block {
  return {
    id,
    conversation_id: 'c1',
    kind: 'message',
    author_type: 'participant',
    author: 'agent',
    content: `Question ${id}`,
    reply_to: null,
    refs: [],
    created_at: '2026-08-01T00:00:00Z',
    meta: {
      asked: [],
      asked_all: true,
      notice_id: 'notice-1',
      options: [{ text: 'A' }, { text: 'B' }],
      answer_log: [],
      ...meta,
    },
  } as unknown as Block
}

const vuetify = createVuetify({ components, directives })

function renderCard(blocks: Block[]) {
  setLocale('zh-CN')
  return render(AskGroupCard, {
    props: { blocks, viewer: 'u1', names: {} },
    global: { plugins: [vuetify, i18n] },
  })
}

const answer = { kind: 'option', option: 'A', note: null, by: 'u1', at: '2026-08-01T00:01:00Z', reply_id: 'r1' }

function tabTexts(tabs: HTMLElement[]): string[] {
  return tabs.map((tab) => tab.textContent?.replace(/\s+/g, ' ').trim() ?? '')
}

describe('AskGroupCard question numbers', () => {
  it.each([2, 3])('shows each question number once for %i questions without headers', (count) => {
    const { getAllByRole } = renderCard(Array.from({ length: count }, (_, i) => question(String(i))))

    expect(tabTexts(getAllByRole('tab'))).toEqual(Array.from({ length: count }, (_, i) => String(i + 1)))
  })

  it('preserves the existing number and header for labelled questions', () => {
    const { getAllByRole } = renderCard([question('a', { header: 'Scope' }), question('b', { header: 'Format' })])

    expect(tabTexts(getAllByRole('tab'))).toEqual(['1Scope', '2Format'])
  })

  it('keeps the number and completion mark when an answer is selected, and still switches questions', async () => {
    const { getAllByRole, getByRole, getByText } = renderCard([question('a'), question('b')])

    await fireEvent.change(getByRole('radio', { name: 'A' }))
    expect(tabTexts(getAllByRole('tab'))).toEqual(['✓1', '2'])

    await fireEvent.click(getByRole('tab', { name: '2' }))
    expect(getByText('Question b')).toBeTruthy()
  })

  it('keeps the number and completion mark for recorded answers', () => {
    const answered = question('a', { answer_log: [answer] })
    const { getAllByRole } = renderCard([answered, question('b')])

    expect(tabTexts(getAllByRole('tab'))).toEqual(['✓1', '2'])
  })
})

describe('AskGroupCard answers arriving from elsewhere', () => {
  it('答案从别处进来时，卡片跟上、按题列出答案，不再画成「未作答」', async () => {
    const { getAllByRole, queryAllByRole, rerender } = renderCard([question('a'), question('b')])
    expect(getAllByRole('radio')).toHaveLength(2)

    // 另一台设备上答的、或者房间里的一条回复被记成了答案：两道题上都记上了，而这张
    // 卡这一屏自己什么也没选。
    await rerender({
      blocks: [question('a', { answer_log: [answer] }), question('b', { answer_log: [answer] })],
      viewer: 'u1',
      names: {},
    })

    expect(queryAllByRole('radio')).toHaveLength(0)
    expect(tabTexts(getAllByRole('tab'))).toEqual(['✓1', '✓2'])
    const recap = document.querySelector('.ask-group__recap')!
    expect(recap.textContent).not.toContain('未作答')
    expect(recap.textContent).toContain('A')
  })

  it('别人把整组都答了，我这边照答案的样子画，不再摆选项', () => {
    const { queryAllByRole, getAllByRole } = renderCard([
      question('a', { answer_log: [{ ...answer, by: 'carol' }] }),
      question('b', { answer_log: [{ ...answer, by: 'carol' }] }),
    ])

    expect(queryAllByRole('radio')).toHaveLength(0)
    expect(tabTexts(getAllByRole('tab'))).toEqual(['✓1', '✓2'])
  })

  it('只答了一部分：还在答题屏，答过的题预填好', async () => {
    const { getAllByRole, getByRole } = renderCard([
      question('a', { answer_log: [{ ...answer, by: 'carol' }] }),
      question('b'),
    ])

    expect((getByRole('radio', { name: 'A' }) as HTMLInputElement).checked).toBe(true)

    await fireEvent.click(getByRole('tab', { name: '2' }))
    expect((getAllByRole('radio') as HTMLInputElement[]).every((r) => !r.checked)).toBe(true)
  })

  it('手上还没交的选择不被别人答的答案冲掉', async () => {
    const { getByRole, rerender } = renderCard([question('a'), question('b')])
    await fireEvent.change(getByRole('radio', { name: 'B' }))

    await rerender({
      blocks: [question('a', { answer_log: [{ ...answer, by: 'carol' }] }), question('b')],
      viewer: 'u1',
      names: {},
    })

    // 还在答题屏，自己选的那个还在。
    expect((getByRole('radio', { name: 'B' }) as HTMLInputElement).checked).toBe(true)
  })
})

describe('AskGroupCard 每题一个叉', () => {
  it('问到我的题，题签那一屏上有 ✕：点它只发这一道', async () => {
    const { getAllByRole, getByRole, emitted } = renderCard([question('a'), question('b')])

    // 卡自己不改状态：请求由面板发（`useAskResponse.skipQuestion`），题块带着
    // 服务端的 `skipped` 回来才换屏。
    await fireEvent.click(getByRole('button', { name: '跳过这道题' }))

    expect((emitted().skip as unknown[][])[0]![0]).toMatchObject({ id: 'a' })
    expect(getAllByRole('radio')).toHaveLength(2)
  })

  it('跳过的题：题签打一横，那一屏上说明「已跳过」，选项收掉', async () => {
    const skipped = question('a', {
      answer_log: [{ kind: 'skipped', option: null, note: null, by: 'u1', at: '2026-08-01T00:01:00Z', reply_id: null }],
    })
    const { getAllByRole, container, queryByRole } = renderCard([skipped, question('b')])

    expect(tabTexts(getAllByRole('tab'))).toEqual(['–1', '2'])
    expect(container.querySelector('.ask-group__hint')?.textContent).toContain('你跳过了这道题')
    expect(queryByRole('radio')).toBeNull()
    // 同组别的题照旧等他答：切过去选项还在。
    await fireEvent.click(getAllByRole('tab')[1]!)
    expect(getAllByRole('radio')).toHaveLength(2)
  })

  it('一道题被答掉或被跳过，不把同组别的题一起变灰', async () => {
    // 别的题被人答了、或者我自己跳过了，都只是那一道的事：同组别的题还各自等各自的
    // 回答，切过去照旧点得动（整组一个叉／一个灰会把它们一起废掉）。
    const theirs = question('a', { answer_log: [{ ...answer, by: 'carol' }] })
    const mine = question('b', {
      answer_log: [{ kind: 'skipped', option: null, note: null, by: 'u1', at: '2026-08-01T00:01:00Z', reply_id: null }],
    })
    const { getAllByRole, getByRole } = renderCard([theirs, mine, question('c')])

    await fireEvent.click(getByRole('tab', { name: '3' }))
    const radios = getAllByRole('radio') as HTMLInputElement[]
    expect(radios).toHaveLength(2)
    expect(radios.every((r) => !r.disabled)).toBe(true)
  })

  it('提问方撤回的那一道：说明一句，不摆选项；同组别的题照旧', async () => {
    const withdrawn = question('a', {
      answer_log: [
        { kind: 'withdrawn', option: null, note: null, by: 'agent', at: '2026-08-01T00:01:00Z', reply_id: null },
      ],
    })
    const { getAllByRole, container, queryByRole } = renderCard([withdrawn, question('b')])

    expect(container.querySelector('.ask-group__hint')?.textContent).toContain('芝士收回了这道题')
    expect(queryByRole('radio')).toBeNull()
    expect(queryByRole('button', { name: '跳过这道题' })).toBeNull()

    await fireEvent.click(getAllByRole('tab')[1]!)
    expect(getAllByRole('radio')).toHaveLength(2)
  })

  it('没问到我的题：没有 ✕，选项也点不动', () => {
    const theirs = question('a', { asked: ['someone-else'], asked_all: false })
    const { container, queryByRole } = renderCard([theirs, question('b')])

    expect(queryByRole('button', { name: '跳过这道题' })).toBeNull()
    expect((container.querySelector('input[type=radio]') as HTMLInputElement).disabled).toBe(true)
  })
})
