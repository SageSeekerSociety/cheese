/**
 * 大表、长格、原文这三件事都出在「屏幕上看到的和文件里的不是一回事」上。这几条盯的
 * 是读者能不能知道自己看到的是哪一段，以及他点出去的那个坐标还准不准 —— 画不下一
 * 整张表是可以的，但画了一部分却不说，读者会把缺的那截当成文件本来就没有。
 */
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it } from 'vitest'

import PreviewSheet from './PreviewSheet.vue'

import { setLocale } from '@/i18n'

beforeEach(() => setLocale('zh-CN'))

const vuetify = createVuetify({ components, directives })

afterEach(cleanup)

type Kind = 'workbook' | 'csv' | 'tsv' | 'ods'

function mount(bytes: Uint8Array, kind: Kind = 'csv') {
  return render(PreviewSheet, {
    props: { data: bytes.buffer as ArrayBuffer, kind },
    global: { plugins: [vuetify] },
  })
}

function utf8(text: string): Uint8Array {
  return new TextEncoder().encode(text)
}

/** 屏幕上那张表的内容，行号和列头不算在内。 */
function grid(container: Element): string[][] {
  return Array.from(container.querySelectorAll('tbody tr')).map((tr) =>
    Array.from(tr.querySelectorAll('td')).map((td) => td.textContent?.trim() ?? '')
  )
}

function note(container: Element): string {
  return container.querySelector('.ps__note')?.textContent?.trim() ?? ''
}

it('超过 500 行的表只画前 500 行，并说清只画了一部分', async () => {
  const lines = ['序号,值']
  for (let i = 1; i <= 600; i += 1) lines.push(`${i},v${i}`)
  const { container } = mount(utf8(`${lines.join('\n')}\n`))

  await waitFor(() => expect(grid(container).length).toBe(500))
  // 画出来的是表头加前 499 行数据 —— 上限数的是画出来的行，不是数据行。
  expect(grid(container)[499][0]).toBe('499')
  expect(note(container)).toContain('只显示了前 500 行')
})

it('超过 64 列的表只画前 64 列，并说清列也被截了', async () => {
  const header = Array.from({ length: 70 }, (_, i) => `c${i}`).join(',')
  const { container } = mount(utf8(`${header}\n`))

  await waitFor(() => expect(container.querySelectorAll('thead .ps__col').length).toBe(64))
  expect(note(container)).toContain('64 列')
})

it('很长的单元格画成截短版，但发出去的仍是整格内容', async () => {
  const long = '甲'.repeat(400)
  const { container, emitted } = mount(utf8(`备注\n${long}\n`))

  await waitFor(() => expect(grid(container).length).toBe(2))
  const shown = grid(container)[1][0]
  expect(shown.length).toBe(301)
  expect(shown.endsWith('…')).toBe(true)
  expect(note(container)).toContain('较长的单元格被截短了')

  await fireEvent.click(container.querySelectorAll('tbody tr:nth-child(2) td')[0])
  // 地址对的是那一个格子，不是它显示成什么样。
  const payload = emitted().cell[0] as { value: string }[]
  expect(payload[0].value).toBe(long)
})

it('超出读取上限的大文件说清表尾可能缺', async () => {
  const big = 'a,b\n'.repeat(300000)
  expect(big.length).toBeGreaterThan(1 << 20)
  const { container } = mount(utf8(big))

  await waitFor(() => expect(note(container)).toContain('表尾可能缺'))
})

it('超过 1 MiB 的 UTF-8 中文表格，切字节不会把最后一个字劈成两半', async () => {
  // 1<<20 不是 3 的倍数，所以按字节上限切下来，最后一个「三」只剩两个字节。留着这
  // 半个字，严格 UTF-8 解码就会抛错 —— 于是一份好好的 UTF-8 文件被当成 GBK，整片
  // 变乱码。切点退到字符边界上，切出来的才是这份文件的一个真前缀。
  const bytes = utf8('三'.repeat(349526))
  expect(bytes.length).toBeGreaterThan(1 << 20)
  expect((1 << 20) % 3).not.toBe(0)

  const { container } = mount(bytes)

  await waitFor(() => expect(grid(container).length).toBe(1))
  // 解错了这里是一串别的汉字，不会正好是「三」。
  expect(grid(container)[0][0]).toBe(`${'三'.repeat(300)}…`)
})

it('切到原文看得到分隔符和换行本身', async () => {
  const { container } = mount(utf8('姓名,分数\n张三,90\n'))

  await waitFor(() => expect(grid(container).length).toBe(2))
  const toggle = await screen.findByRole('button', { name: /看原文/ })
  await fireEvent.click(toggle)

  expect(container.querySelector('.ps__source')?.textContent).toBe('姓名,分数\n张三,90\n')
  expect(container.querySelector('tbody')).toBeNull()
})

it('换一份文件就回到表格视图', async () => {
  const { container, rerender } = mount(utf8('姓名,分数\n张三,90\n'))

  await waitFor(() => expect(grid(container).length).toBe(2))
  await fireEvent.click(await screen.findByRole('button', { name: /看原文/ }))
  expect(container.querySelector('.ps__source')).toBeTruthy()

  await rerender({
    data: utf8('城市,人口\n上海,2487\n').buffer as ArrayBuffer,
    kind: 'csv' as const,
  })

  // 留在原文视图会看见上一个文件的内容，而读者以为自己在看新文件的原文。
  await waitFor(() => expect(container.querySelector('.ps__source')).toBeNull())
  expect(grid(container)[1]).toEqual(['上海', '2487'])
})

it('tsv 按制表符切，不再去猜分隔符', async () => {
  // 第一行里逗号比制表符多，猜的话会切成两列。
  const { container } = mount(utf8('姓名,别名\t分数\na,b\t90\n'), 'tsv')

  await waitFor(() => expect(grid(container).length).toBe(2))
  expect(grid(container)[0]).toEqual(['姓名,别名', '分数'])
})

it('ods 说清这条路走不通，而不是抛一句读不懂的错', async () => {
  mount(utf8('PK\u0003\u0004'), 'ods')

  expect(await screen.findByText(/OpenDocument 表格/)).toBeTruthy()
})

it('老版 .xls（OLE2 容器）也说清打不开', async () => {
  // 真 .xls 的头八个字节就是这些 —— 它后面跟什么不影响判断，认的就是容器魔数。
  const ole2 = Uint8Array.from([0xd0, 0xcf, 0x11, 0xe0, 0xa1, 0xb1, 0x1a, 0xe1, 0, 0, 0, 0])
  mount(ole2, 'workbook')

  expect(await screen.findByText(/老版 Excel 表格/)).toBeTruthy()
})
