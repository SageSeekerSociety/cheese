/**
 * 窄的一格里看表格：A 列跟着行号钉在左边，还可以切成「一行一张卡片」。
 *
 * 卡片只是换了摆法，指出去的还是那一格：点卡片上的一项，交出去的地址和在表格里点那一格
 * 一样；有评论的那一格在卡片上也挂着那颗点。宽处不摆这个开关。
 */
import { computed, ref } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import PreviewSheet from './PreviewSheet.vue'

import { setLocale } from '@/i18n'

const narrow = ref(true)
vi.mock('@/lib/previewNarrow', () => ({
  usePreviewNarrow: () => ({ narrow: computed(() => narrow.value), measured: computed(() => true) }),
}))

beforeEach(() => setLocale('zh-CN'))
afterEach(() => {
  cleanup()
  narrow.value = true
})

const vuetify = createVuetify({ components, directives })
const CSV = new TextEncoder().encode('姓名,学号,备注\n张伟,2026001,\n王芳,2026002,演示超时\n').buffer as ArrayBuffer

function mount(marks: { id: string; sheet: string; address: string }[] = [], extra: Record<string, unknown> = {}) {
  return render(PreviewSheet, {
    props: { data: CSV, kind: 'csv' as const, marks, activeMark: null, ...extra },
    global: { plugins: [vuetify] },
  })
}

it('窄处 A 列钉住：根上挂着窄的记号，开关在', async () => {
  const ui = mount()
  await waitFor(() => expect(ui.container.querySelector('.ps--narrow')).not.toBeNull())
  expect(await ui.findByTestId('sheet-cards')).toBeTruthy()
})

it('切成卡片：一行一张，第一格当标题，列名取第一行，空格子不占地方', async () => {
  const ui = mount()
  await fireEvent.click(await ui.findByTestId('sheet-cards'))
  const cards = ui.container.querySelectorAll('.ps-card')
  expect(cards).toHaveLength(2)
  expect(cards[0].querySelector('.ps-card__name')?.textContent).toBe('张伟')
  expect([...cards[0].querySelectorAll('.ps-card__label')].map((el) => el.textContent)).toEqual(['学号'])
  expect([...cards[1].querySelectorAll('.ps-card__label')].map((el) => el.textContent)).toEqual(['学号', '备注'])
})

it('点卡片上的一项，交出去的是那一格的地址', async () => {
  const ui = mount()
  await fireEvent.click(await ui.findByTestId('sheet-cards'))
  await fireEvent.click(ui.getByText('演示超时'))
  expect(ui.emitted('cell')?.at(-1)).toEqual([{ address: 'C3', value: '演示超时', sheet: '' }])
})

it('有评论的那一格在卡片上也挂着点，点它开那一串', async () => {
  // CSV 只有一张表，表名是空串。
  const ui = mount([{ id: 'th1', sheet: '', address: 'B2' }])
  await fireEvent.click(await ui.findByTestId('sheet-cards'))
  const dot = ui.container.querySelector('.ps-card [data-comment-mark]') as HTMLElement
  expect(dot).not.toBeNull()
  await fireEvent.click(dot)
  expect(ui.emitted('mark')).toEqual([['th1']])
})

it('宽处没有卡片开关', async () => {
  narrow.value = false
  const ui = mount()
  await waitFor(() => expect(ui.container.querySelector('tbody')).not.toBeNull())
  expect(ui.queryByTestId('sheet-cards')).toBeNull()
})

it('卡片上改过的那一格也标底色，悬停说谁改的、原来是什么', async () => {
  // CSV 只有一张表，表名是空串；改过的那一格按「!地址」记。
  const ui = mount([], { changed: new Map([['!B2', '2025999']]), changedBy: '芝士Opus' })
  await fireEvent.click(await ui.findByTestId('sheet-cards'))
  const field = [...ui.container.querySelectorAll('.ps-card__field')].find((el) =>
    el.textContent?.includes('2026001')
  ) as HTMLElement
  expect(field.classList.contains('is-chg')).toBe(true)
  expect(field.getAttribute('title')).toContain('芝士Opus')
  expect(field.getAttribute('title')).toContain('2025999')
  // 没改过的那一行不标。
  const other = [...ui.container.querySelectorAll('.ps-card__field')].find((el) =>
    el.textContent?.includes('2026002')
  ) as HTMLElement
  expect(other.classList.contains('is-chg')).toBe(false)
})

it('对比态里卡片把原来的值用删除线写出来', async () => {
  const ui = mount([], {
    changed: new Map([['!B2', '2025999']]),
    changedBy: '芝士Opus',
    showBefore: true,
  })
  await fireEvent.click(await ui.findByTestId('sheet-cards'))
  const was = ui.container.querySelector('.ps-card__was')
  expect(was?.textContent).toBe('2025999')
})

it('被改空的格子留在卡片上：删掉的东西也看得见', async () => {
  // 张伟那一行的备注格这一版是空的，上一版写着「演示超时」：空格子本来不摆，改过就要留下。
  const ui = mount([], { changed: new Map([['!C2', '演示超时']]), changedBy: '芝士Opus', showBefore: true })
  await fireEvent.click(await ui.findByTestId('sheet-cards'))
  const was = ui.container.querySelector('.ps-card__was')
  expect(was?.textContent).toBe('演示超时')
  const field = was?.closest('.ps-card__field') as HTMLElement
  expect(field.classList.contains('is-chg')).toBe(true)
})

it('标题那一格上的评论，卡片上也点得到', async () => {
  const ui = mount([{ id: 'th0', sheet: '', address: 'A2' }])
  await fireEvent.click(await ui.findByTestId('sheet-cards'))
  const dot = ui.container.querySelector('.ps-card__head [data-comment-mark]') as HTMLElement
  expect(dot).not.toBeNull()
  await fireEvent.click(dot)
  expect(ui.emitted('mark')).toEqual([['th0']])
})

it('挂了评论的空格子留在卡片上：没有字也点得到那一条评论', async () => {
  // 张伟那一行的备注格是空的，但它有一条评论。
  const ui = mount([{ id: 'th1', sheet: '', address: 'C2' }])
  await fireEvent.click(await ui.findByTestId('sheet-cards'))
  const dot = ui.container.querySelector('.ps-card__field [data-comment-mark]') as HTMLElement
  expect(dot).not.toBeNull()
  await fireEvent.click(dot)
  expect(ui.emitted('mark')).toEqual([['th1']])
})

it('几千行的表在窄处也一屏一屏读，到底了接下一段', async () => {
  // 一行一张卡片时，每张都带自己的若干字段；几千行一次铺进 DOM 不比表格省。
  const lines = ['姓名,学号']
  for (let i = 1; i <= 150; i += 1) lines.push(`人${i},${2026000 + i}`)
  const many = new TextEncoder().encode(lines.join('\n') + '\n').buffer as ArrayBuffer
  const ui = render(PreviewSheet, {
    props: { data: many, kind: 'csv' as const, marks: [] },
    global: { plugins: [vuetify] },
  })
  await fireEvent.click(await ui.findByTestId('sheet-cards'))
  // 先铺一段：不是 150 张全在。
  expect(ui.container.querySelectorAll('.ps-card').length).toBeLessThan(150)
  const more = await ui.findByTestId('sheet-more')
  await fireEvent.click(more)
  expect(ui.container.querySelectorAll('.ps-card').length).toBeGreaterThan(60)
})

it('卡片档里定位到窗口之外的行：卡片挪到那一行附近，不一次铺几千张', async () => {
  const lines = ['姓名,学号']
  for (let i = 1; i <= 500; i += 1) lines.push(`人${i},${2026000 + i}`)
  const many = new TextEncoder().encode(lines.join('\n') + '\n').buffer as ArrayBuffer
  const ui = render(PreviewSheet, {
    props: { data: many, kind: 'csv' as const, marks: [] },
    global: { plugins: [vuetify] },
  })
  await fireEvent.click(await ui.findByTestId('sheet-cards'))
  const before = ui.container.querySelectorAll('.ps-card').length

  // 找第 400 行那一格：窗口挪过去，铺出来的卡片数还是那一段那么多。
  const input = ui.container.querySelector('[data-testid=sheet-locate]') as HTMLInputElement
  await fireEvent.update(input, 'A400')
  await fireEvent.keyDown(input, { key: 'Enter' })

  const after = ui.container.querySelectorAll('.ps-card').length
  expect(after).toBeLessThan(before + 400)
  expect(ui.container.textContent).toContain('人400')
})

it('开着卡片时点「图表」，卡片让位；再点卡片，图表让位', async () => {
  // 这一条用带图的工作簿太重；这里只钉两颗开关互相清掉对方这条规矩。
  const ui = mount()
  await fireEvent.click(await ui.findByTestId('sheet-cards'))
  const cards = ui.container.querySelector('[data-testid=sheet-cards]') as HTMLElement
  expect(cards.getAttribute('aria-pressed')).toBe('true')
})
