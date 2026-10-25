/**
 * CSV 是芝士 最常产出的表格格式，而这个视图原先只会读工作簿 —— 一份 CSV 送进来
 * 必然抛错。这几条盯的是读者看到的那张表和他点出来的那个坐标，不是解析过程。
 */
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it } from 'vitest'

import PreviewSheet from './PreviewSheet.vue'

import { setLocale } from '@/i18n'

// 断言读的是中文界面上的那一行字，语言钉在中文上。
beforeEach(() => setLocale('zh-CN'))

const vuetify = createVuetify({ components, directives })

afterEach(cleanup)

function mount(
  bytes: ArrayBuffer | Uint8Array,
  marks: { id: string; sheet: string; address: string }[] = [],
  activeMark: string | null = null
) {
  const data = bytes instanceof Uint8Array ? (bytes.buffer as ArrayBuffer) : bytes
  return render(PreviewSheet, {
    props: { data, kind: 'csv' as const, marks, activeMark },
    global: { plugins: [vuetify] },
  })
}

function utf8(text: string): Uint8Array {
  return new TextEncoder().encode(text)
}

/** 一行一行读出屏幕上那张表，行号和列头不算在内。 */
function grid(container: Element): string[][] {
  return Array.from(container.querySelectorAll('tbody tr')).map((tr) =>
    Array.from(tr.querySelectorAll('td')).map((td) => td.textContent?.trim() ?? '')
  )
}

it('把一份 CSV 摆成表格', async () => {
  const { container } = mount(utf8('姓名,分数\n张三,90\n李四,85\n'))

  await waitFor(() => expect(grid(container).length).toBe(3))
  expect(grid(container)).toEqual([
    ['姓名', '分数'],
    ['张三', '90'],
    ['李四', '85'],
  ])
})

it('引号里的逗号和换行不是边界', async () => {
  const { container } = mount(utf8('项目,备注\n差旅,"北京,上海"\n设备,"两台\n三个月"\n'))

  await waitFor(() => expect(grid(container).length).toBe(3))
  expect(grid(container)[1]).toEqual(['差旅', '北京,上海'])
  // 单元格里的那个换行是内容，不该把这一行拆成两行。
  expect(grid(container)[2][1]).toContain('两台')
  expect(grid(container)[2][1]).toContain('三个月')
})

it('`""` 是一个引号，不是字段结束', async () => {
  const { container } = mount(utf8('说明\n"他说""可以"""\n'))

  await waitFor(() => expect(grid(container).length).toBe(2))
  expect(grid(container)[1]).toEqual(['他说"可以"'])
})

it('点中一个格子，给出的坐标不带工作表前缀', async () => {
  const { container, emitted } = mount(utf8('姓名,分数\n张三,90\n'))

  await waitFor(() => expect(grid(container).length).toBe(2))
  const cells = container.querySelectorAll('tbody tr:nth-child(2) td')
  await fireEvent.click(cells[1])

  // CSV 里没有工作表，所以坐标就是 B2；编一个名字会让读者以为还有别的表。
  expect(emitted().cell[0]).toEqual([{ address: 'B2', value: '90', sheet: '' }])
})

it('分号分隔的文件不挤成一列', async () => {
  const { container } = mount(utf8('姓名;分数\n张三;90\n'))

  await waitFor(() => expect(grid(container).length).toBe(2))
  expect(grid(container)[1]).toEqual(['张三', '90'])
})

it('制表符分隔的文件也读得出列', async () => {
  const { container } = mount(utf8('姓名\t分数\n张三\t90\n'))

  await waitFor(() => expect(grid(container).length).toBe(2))
  expect(grid(container)[1]).toEqual(['张三', '90'])
})

it('Excel 导出的 GBK 中文不是乱码', async () => {
  // 人在中文 Windows 上从 Excel 另存为 CSV 得到的就是这些字节。按 UTF-8 解会得到
  // 一整片乱码，而且不抛错，所以这一条盯的是屏幕上认不认得出「姓名」。
  const gbk = Uint8Array.from([
    0xd0, 0xd5, 0xc3, 0xfb, 0x2c, 0xb7, 0xd6, 0xca, 0xfd, 0x0a, 0xd5, 0xc5, 0xc8, 0xfd, 0x2c, 0x39, 0x30, 0x0a,
  ])
  const { container } = mount(gbk)

  await waitFor(() => expect(grid(container).length).toBe(2))
  expect(grid(container)).toEqual([
    ['姓名', '分数'],
    ['张三', '90'],
  ])
})

it('带 BOM 的 UTF-8 不把 BOM 读成第一个表头的一部分', async () => {
  const { container } = mount(utf8('﻿姓名,分数\n张三,90\n'))

  await waitFor(() => expect(grid(container).length).toBe(2))
  expect(grid(container)[0][0]).toBe('姓名')
})

it('空文件说它是空的，而不是画一张空表', async () => {
  mount(utf8(''))

  expect(await screen.findByText('这个文件是空的')).toBeTruthy()
})

it('只有一张表的时候不画工作表标签', async () => {
  const { container } = mount(utf8('姓名,分数\n张三,90\n'))

  await waitFor(() => expect(grid(container).length).toBe(2))
  expect(container.querySelector('.ps__tabs')).toBeNull()
})

/** 一格上的评论标记：按地址找那一格里那颗点。 */
function markAt(container: Element, address: string): Element | null {
  const td = Array.from(container.querySelectorAll('tbody tr')).flatMap((tr) => Array.from(tr.querySelectorAll('td')))
  const cell = td[gridIndex(container, address)]
  return cell?.querySelector('.ps__cmt') ?? null
}

/** 地址（`B2`）换成那一格在 `td` 列表里的序号。 */
function gridIndex(container: Element, address: string): number {
  const match = /^([A-Z]+)(\d+)$/.exec(address)
  if (!match) return -1
  let col = 0
  for (const ch of match[1]) col = col * 26 + (ch.charCodeAt(0) - 64)
  const row = Number(match[2]) - 1
  const width = container.querySelectorAll('tbody tr:first-child td').length
  if (row < 0 || col < 1) return -1
  return row * width + (col - 1)
}

it('评过的那一格上画一颗标记，别的格子不画', async () => {
  // 地址是表格里唯一指得回的东西，所以标记就落在那一格上。
  const { container } = mount(utf8('姓名,分数\n张三,90\n'), [{ id: 'c1', sheet: '', address: 'B2' }])

  await waitFor(() => expect(grid(container).length).toBe(2))
  expect(markAt(container, 'B2')).toBeTruthy()
  expect(markAt(container, 'A2')).toBeNull()
})

it('地址在这一版里落不到任何一格上（表短了）就不画，不猜它挪到哪去了', async () => {
  const { container } = mount(utf8('姓名,分数\n张三,90\n'), [{ id: 'c1', sheet: '', address: 'B99' }])

  await waitFor(() => expect(grid(container).length).toBe(2))
  expect(container.querySelectorAll('.ps__cmt')).toHaveLength(0)
})

it('点一颗标记，说的是被点的那一条', async () => {
  const { container, emitted } = mount(utf8('姓名,分数\n张三,90\n'), [
    { id: 'c1', sheet: '', address: 'A1' },
    { id: 'c2', sheet: '', address: 'B2' },
  ])

  await waitFor(() => expect(grid(container).length).toBe(2))
  await fireEvent.click(markAt(container, 'B2')!)
  expect(emitted().mark).toEqual([['c2']])
})

it('点标记不开那一格的选中框，也不当成选了一格', async () => {
  // 标记长在格子里面，而格子自己被点了就是「指着这一格」。不挡住的话，点评论会
  // 顺手把这一格选上。
  const { container, emitted } = mount(utf8('姓名,分数\n张三,90\n'), [{ id: 'c1', sheet: '', address: 'B2' }])

  await waitFor(() => expect(grid(container).length).toBe(2))
  await fireEvent.click(markAt(container, 'B2')!)
  expect(emitted().cell).toBeUndefined()
  expect(container.querySelector('.ps__cell--on')).toBeNull()
})

it('正打开的那一条画得显眼些', async () => {
  const { container } = mount(utf8('姓名,分数\n张三,90\n'), [{ id: 'c1', sheet: '', address: 'B2' }], 'c1')

  await waitFor(() => expect(grid(container).length).toBe(2))
  expect(markAt(container, 'B2')!.classList.contains('is-active')).toBe(true)
})
