/**
 * AdminQuestionTable（「答不上来的问题」）。
 *
 * 这张表是**给写文档的人**的清单，不是用户报表，所以这里钉的第一件事是**隐私的底线**：
 * 表里只有问题原文、出处页面、次数三列。第三条用例故意往行里塞一个 `user` 字段 ——
 * 接口今天不发它（后端 `_unanswered` 只选三列），但**将来有人加了、页面顺手画出来**，
 * 就是把「这个人在问什么」交到了平台管理员手上。这一条断言就是那次改动的刹车。
 *
 * 另外两件是读法：
 *   * 问题原文**不截断** —— 截成省略号之后这张表只剩「有 50 条」这一个信息；
 *   * 页面 slug 缺失画破折号，不画空串（空格读起来像加载失败）。
 */
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render } from '@testing-library/vue'
import { describe, expect, it, vi } from 'vitest'

vi.mock('vue-i18n', async (importOriginal) => {
  const actual = await importOriginal<typeof import('vue-i18n')>()
  return { ...actual, useI18n: () => ({ t: (key: string) => key }) }
})

import AdminQuestionTable from './AdminQuestionTable.vue'

const ROWS = [
  { question: '怎么把项目里的成员换成管理员？', page: 'quickstart', count: 7 },
  { question: '导出的时候为什么少了两条记录，是分页的问题吗，还是要翻到最后一次', page: null, count: 2 },
]

function mount(props: Record<string, unknown> = {}) {
  return render(AdminQuestionTable, {
    props: { title: '答不上来的问题', rows: ROWS, empty: '没有答不上来的', ...props },
    // 卡片里的 `AdminNoteTip` / `AdminEmptyState` 是 Vuetify 组件，没有插件就报
    // 「Could not find defaults instance」。
    global: { plugins: [createVuetify({ components, directives })] },
  })
}

describe('AdminQuestionTable', () => {
  it('只有三列：问题、出处页面、次数', () => {
    const { container } = mount()
    const heads = Array.from(container.querySelectorAll('th')).map((el) => el.textContent?.trim())
    expect(heads).toEqual([
      'featureStats.unanswered.column.question',
      'featureStats.unanswered.column.page',
      'featureStats.unanswered.column.count',
    ])
    expect(container.querySelectorAll('tbody tr')).toHaveLength(2)
  })

  it('行里多出来的字段（比如谁问的）不许画出来', () => {
    const { container } = mount({
      rows: [{ question: '这一页怎么改？', page: 'index', count: 1, user: 'alice' }],
    })
    expect(container.textContent).not.toContain('alice')
    // 每行三格：多一列就会在这里长出来。
    expect(container.querySelectorAll('tbody td')).toHaveLength(3)
  })

  it('问题原文整段画出来，不截断', () => {
    const { container } = mount()
    const cells = Array.from(container.querySelectorAll('.aqt__cell--q'))
    expect(cells[0].textContent?.trim()).toBe('怎么把项目里的成员换成管理员？')
    expect(cells[1].textContent?.trim()).toBe(ROWS[1].question)
  })

  it('没记下出处页面的行画破折号，不画空串', () => {
    const { container } = mount()
    const pages = Array.from(container.querySelectorAll('.aqt__cell--page')).map((el) => el.textContent?.trim())
    expect(pages).toEqual(['quickstart', '—'])
  })

  it('次数按千分位分组', () => {
    const { container } = mount({ rows: [{ question: 'q', page: null, count: 12345 }] })
    expect(container.querySelector('.aqt__cell--n')?.textContent?.trim()).toBe('12,345')
  })

  it('一行都没有时画那句话，不画一张空表', () => {
    const { container } = mount({ rows: [] })
    expect(container.textContent).toContain('没有答不上来的')
    expect(container.querySelector('table')).toBeNull()
  })

  it('加载中只画骨架（空表和骨架是两句话）', () => {
    const { container } = mount({ loading: true })
    expect(container.querySelectorAll('.aqt__bone').length).toBeGreaterThan(0)
    expect(container.querySelector('table')).toBeNull()
    expect(container.textContent).not.toContain('没有答不上来的')
  })
})
