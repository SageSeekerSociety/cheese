// 题目的历史版本：新的在前，每一版写明第几版、谁改的、改了什么、那一版手上的文件；
// 拿得到材料的人取得回每一版的文件，拿不到的只看到名字；地址指着哪一版就标出哪一版。
import type { TaskVersionData } from '@/types/taskFiles'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, within } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it } from 'vitest'

import VersionsView from './VersionsView.vue'

import i18n, { setLocale } from '@/i18n'

afterEach(cleanup)
beforeEach(() => setLocale('zh-CN'))

const author = { id: 1, username: 'teacher', nickname: '王老师' }

const VERSIONS: TaskVersionData[] = [
  {
    number: 3,
    at: Date.UTC(2026, 9, 20, 2, 0),
    author,
    changes: { files: { replaced: ['数据.csv'] } },
    files: [
      { name: '数据.csv', size: 2048 },
      { name: '草稿.txt', size: null },
    ],
  },
  {
    number: 2,
    at: Date.UTC(2026, 9, 19, 2, 0),
    author,
    changes: { description: true, starter: 'added' },
    files: [{ name: '数据.csv', size: 1024 }],
  },
  { number: 1, at: Date.UTC(2026, 9, 18, 2, 0), author: null, changes: { published: true }, files: null },
]

function mount(extra: Record<string, unknown> = {}) {
  return render(VersionsView, {
    props: { versions: VERSIONS, canDownload: true, ...extra },
    global: { plugins: [createVuetify({ components, directives }), i18n] },
  })
}

const items = (view: ReturnType<typeof mount>) => view.getAllByTestId('task-version')

it('新的在前：每一版写明第几版、谁改的、改了什么', () => {
  const view = mount()
  const [third, second, first] = items(view)
  expect(within(third).getByText('第 3 版')).toBeTruthy()
  expect(within(third).getByText('当前版本')).toBeTruthy()
  expect(third.textContent).toContain('王老师')
  expect(within(third).getByText('替换 数据.csv')).toBeTruthy()
  expect(within(second).getByText('改了说明、新增起始代码')).toBeTruthy()
  expect(within(first).getByText('题目发布')).toBeTruthy()
  expect(within(second).queryByText('当前版本')).toBeNull()
})

it('下载取的是那一版的那一份', async () => {
  const view = mount()
  const second = items(view)[1]
  await fireEvent.click(within(second).getByRole('button', { name: '下载' }))
  expect(view.emitted('download')).toEqual([[2, { name: '数据.csv', size: 1024 }]])
})

it('后来删掉的材料只剩名字，没有下载；没记下文件的版本照实说', () => {
  const view = mount()
  const [third, , first] = items(view)
  expect(within(third).getByText('草稿.txt')).toBeTruthy()
  expect(within(third).getByText('已删除')).toBeTruthy()
  expect(within(third).getAllByRole('button', { name: '下载' })).toHaveLength(1)
  expect(within(first).getByText('这一版比较早，当时还没有记录文件')).toBeTruthy()
})

it('拿不到材料的人：「开始做这道题之后才能下载」在上方说一次，不在每一行重复', () => {
  const view = mount({ canDownload: false })
  expect(view.getAllByText('开始做这道题之后才能下载')).toHaveLength(1)
})

it('点文件名打开那一版的那一份，和「说明」页签一样预览', async () => {
  const view = mount()
  const second = items(view)[1]
  await fireEvent.click(within(second).getByRole('button', { name: '数据.csv' }))
  expect(view.emitted('open')).toEqual([[2, { name: '数据.csv', size: 1024 }]])
})

it('拿不到材料的人看得到每一版的文件名，没有下载', () => {
  const view = mount({ canDownload: false })
  expect(view.queryByRole('button', { name: '下载' })).toBeNull()
  expect(items(view)[0].textContent).toContain('数据.csv')
})

it('地址指着哪一版，就标出哪一版', () => {
  const view = mount({ focus: 2 })
  const [third, second] = items(view)
  expect(second.getAttribute('aria-current')).toBe('true')
  expect(third.getAttribute('aria-current')).toBeNull()
})

it('一版都没有时说暂无', () => {
  const view = mount({ versions: [] })
  expect(view.getByText('暂无历史版本')).toBeTruthy()
})
