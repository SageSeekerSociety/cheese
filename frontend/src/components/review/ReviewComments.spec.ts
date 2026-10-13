/** 「改动」里的批注：待审阅时在任意一行写批注（按住 Shift 选一段）、写修改建议；
 *  批注挂在它说的那几行下面，那几行在这一版里不在了就挂在文件头上；存的时候和别人
 *  的修改重叠了，逐处选版本，存下去的是选出来的那一份。 */
import type { FileDiff } from '../../lib/diff'
import type { DiffReview, MergeRegion, ReviewComment } from '../../types/reviewComment'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { beforeEach, describe, expect, it } from 'vitest'

import ChangesDiffList from '../panels/ChangesDiffList.vue'

import ReviewMergePicker from './ReviewMergePicker.vue'

import i18n, { setLocale } from '@/i18n'

beforeEach(() => setLocale('zh-CN'))

const DIFF: FileDiff = {
  path: 'src/app.ts',
  status: 'modified',
  added: 2,
  removed: 1,
  body: [
    'diff --git a/src/app.ts b/src/app.ts',
    '--- a/src/app.ts',
    '+++ b/src/app.ts',
    '@@ -1,3 +1,4 @@',
    ' const a = 1',
    '-const b = 2',
    '+const b = 3',
    '+const c = 4',
    ' const d = 5',
  ].join('\n'),
} as FileDiff

function sent(over: Partial<ReviewComment>): ReviewComment {
  return {
    id: 's-1',
    author: 'bob',
    path: 'src/app.ts',
    line_start: 2,
    line_end: 2,
    line_text: 'const b = 3',
    place: 'L2',
    current_line: 2,
    body: '这里要改',
    suggestion: null,
    parent_id: null,
    state: 'sent',
    card_id: 'card-1',
    sent_at: '2026-08-01T00:00:00Z',
    outcome: 'handled',
    outcome_note: '改好了',
    created_at: '2026-08-01T00:00:00Z',
    ...over,
  }
}

function mount(review: Partial<DiffReview>) {
  return render(ChangesDiffList, {
    props: {
      diffs: [DIFF],
      review: { comments: [], writable: true, me: 'alice', busy: false, agentName: '芝士', ...review },
    },
    global: { plugins: [createVuetify({ components, directives }), i18n] },
  })
}

const plusAt = (container: Element, line: number) =>
  container.querySelector(`button[aria-label="在第 ${line} 行写批注"]`) as HTMLButtonElement | null
const buttonNamed = (container: Element, label: string) =>
  Array.from(container.querySelectorAll('button')).find((b) => b.textContent?.trim() === label)

describe('写批注', () => {
  it('选中一段行写批注：送出的是那几行和它们现在的字', async () => {
    const { container, emitted } = mount({})
    await fireEvent.click(plusAt(container, 2)!)
    await fireEvent.click(plusAt(container, 3)!, { shiftKey: true })
    await fireEvent.update(container.querySelector('.comment-box textarea')!, '两行一起看')
    await fireEvent.click(buttonNamed(container, '添加批注')!)

    expect(emitted().comment[0]).toEqual([
      {
        path: 'src/app.ts',
        line_start: 2,
        line_end: 3,
        line_text: 'const b = 3\nconst c = 4',
        body: '两行一起看',
        suggestion: null,
      },
    ])
  })

  it('修改建议从选中的那几行开始改', async () => {
    const { container, emitted } = mount({})
    await fireEvent.click(plusAt(container, 2)!)
    await fireEvent.click(buttonNamed(container, '修改建议')!)
    const box = container.querySelector('.comment-box__suggestion') as HTMLTextAreaElement
    expect(box.value).toBe('const b = 3')
    await fireEvent.update(box, 'const b = 30')
    await fireEvent.click(buttonNamed(container, '添加批注')!)

    expect((emitted().comment[0] as unknown[])[0]).toMatchObject({ suggestion: 'const b = 30', line_start: 2 })
  })

  it('不在待审阅时没有写批注的入口', () => {
    const { container } = mount({ writable: false })
    expect(plusAt(container, 2)).toBeNull()
  })
})

describe('批注挂在哪', () => {
  it('上一轮的批注挂在它说的那一行下面，带着芝士说的处理结果', () => {
    const { container } = mount({ comments: [sent({ current_line: 3, line_start: 2, line_end: 2 })] })
    const card = container.querySelector('.comment-card')!
    expect(card.textContent).toContain('改好了')
    // 紧跟在新版本第 3 行后面。
    const rows = Array.from(container.querySelectorAll('.diff-line, .comment-card'))
    const at = rows.indexOf(card)
    expect(rows[at - 1].textContent).toContain('const c = 4')
  })

  it('那几行在这一版里不在了：挂在文件头上，说原位置已删除', () => {
    const { container } = mount({ comments: [sent({ current_line: null, outcome: null, outcome_note: null })] })
    const card = container.querySelector('.comment-card')!
    expect(card.textContent).toContain('原位置已删除')
    const firstLine = container.querySelector('.diff-line')!
    expect(card.compareDocumentPosition(firstLine) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })
})

describe('存的时候重叠了', () => {
  const REGIONS: MergeRegion[] = [
    { kind: 'same', text: 'a\n' },
    { kind: 'conflict', base: 'b\n', mine: 'mine\n', theirs: 'theirs\n' },
    { kind: 'same', text: 'c\n' },
  ]
  const mountPicker = () =>
    render(ReviewMergePicker, {
      props: { regions: REGIONS, agentName: '芝士' },
      global: { plugins: [createVuetify({ components, directives }), i18n] },
    })

  it('不动就存你的那一份', async () => {
    const { container, emitted } = mountPicker()
    await fireEvent.click(buttonNamed(container, '保存')!)
    expect(emitted().resolve[0]).toEqual(['a\nmine\nc\n'])
  })

  it('选了它的，存下去的就是它的那一处', async () => {
    const { container, emitted } = mountPicker()
    await fireEvent.click(container.querySelectorAll('.merge-picker__col--pick')[1])
    await fireEvent.click(buttonNamed(container, '保存')!)
    expect(emitted().resolve[0]).toEqual(['a\ntheirs\nc\n'])
  })
})
