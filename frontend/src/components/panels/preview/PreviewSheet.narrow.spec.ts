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

function mount(marks: { id: string; sheet: string; address: string }[] = []) {
  return render(PreviewSheet, {
    props: { data: CSV, kind: 'csv' as const, marks, activeMark: null },
    global: { plugins: [vuetify] },
  })
}

it('窄处 A 列钉住：根上挂着窄的记号，开关在', async () => {
  const ui = mount()
  await waitFor(() => expect(ui.container.querySelector('.ps--narrow')).not.toBeNull())
  expect(ui.getByTestId('sheet-cards')).toBeTruthy()
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
