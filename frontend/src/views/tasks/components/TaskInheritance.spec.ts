// 「会继承什么」这一块 (#944)：屏幕上那几项**就是**接口给的那几个值。
//
// 后端的另一半在 `backend/tests/integration/test_task_inheritance.py`：那边断言
// 接口给的 `teaching` 等于 `protocol.resolve()` 的合成结果，并且去掉一层它跟着
// 变。这里接住前半段 —— 组件不自己算、不自己拼，渲染出来的就是那份数据。两段合
// 起来才是「界面数据 = 合成结果」。
import type { TaskInheritanceData } from '@/network/api/tasks/types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render } from '@testing-library/vue'
import { afterEach, describe, expect, it } from 'vitest'

import TaskInheritance from './TaskInheritance.vue'

import i18n, { setLocale } from '@/i18n'

function payload(overrides: Partial<TaskInheritanceData> = {}): TaskInheritanceData {
  return {
    taskId: 7,
    spaceId: 3,
    resourcePack: { compute_credits: 500 },
    teaching: {
      systemPrompt: '第 {current_week} 周：循环。',
      currentWeek: 5,
      allowedTopics: ['循环', '数组'],
      avoidInCode: ['递归'],
      materialIds: [],
      knowledgeIds: [],
      source: 'task',
    },
    materials: [],
    ...overrides,
  }
}

function mount(data: TaskInheritanceData | null) {
  return render(TaskInheritance, {
    props: { inheritance: data },
    global: { plugins: [createVuetify({ components, directives }), i18n] },
  })
}

describe('建项目会继承什么', () => {
  afterEach(() => cleanup())

  it('把接口给的合成结果原样摆出来', () => {
    setLocale('zh-CN')
    mount(payload())

    const text = document.body.textContent ?? ''
    expect(text).toContain(i18n.global.t('tasks.inheritance.title'))
    expect(text).toContain(String(500))
    // 那一周就是接口给的 currentWeek —— 组件不做二次解析。
    expect(text).toContain(i18n.global.t('tasks.inheritance.week', { n: 5 }))
    expect(text).toContain('循环')
    expect(text).toContain('递归')
    // 来源层单独说一句，不是一个只有结果的字符串。
    expect(text).toContain(i18n.global.t('tasks.inheritance.from.task'))
  })

  it('来源层跟着数据走：项目集那一份就说项目集', () => {
    setLocale('zh-CN')
    mount(
      payload({
        teaching: { systemPrompt: null, currentWeek: 3, allowedTopics: [], avoidInCode: [], source: 'category' },
      })
    )

    const text = document.body.textContent ?? ''
    expect(text).toContain(i18n.global.t('tasks.inheritance.from.category'))
    expect(text).not.toContain(i18n.global.t('tasks.inheritance.from.task'))
  })

  it('资料清单列出名字，一件也没有时也说一句', () => {
    setLocale('zh-CN')
    const { unmount } = mount(
      payload({
        materials: [
          {
            id: 11,
            name: 'L08 讲义.pdf',
            type: 'file',
            visibility: 'members',
            size: 1,
            mime: 'application/pdf',
            uploaderId: 1,
            createdAt: 0,
            downloadCount: 0,
          },
        ],
      })
    )
    expect(document.body.textContent).toContain('L08 讲义.pdf')
    unmount()

    mount(payload({ materials: [] }))
    expect(document.body.textContent).toContain(i18n.global.t('tasks.inheritance.materialsNone'))
  })

  it('四层都没说时不编一句话：没有指导、没有来源层', () => {
    setLocale('zh-CN')
    mount(
      payload({
        resourcePack: {},
        teaching: { systemPrompt: null, currentWeek: null, allowedTopics: [], avoidInCode: [], source: null },
      })
    )

    const text = document.body.textContent ?? ''
    expect(text).toContain(i18n.global.t('tasks.inheritance.guidanceNone'))
    expect(text).toContain(i18n.global.t('tasks.inheritance.packNone'))
    expect(text).not.toContain(i18n.global.t('tasks.inheritance.from.space'))
  })

  it('英文界面下不落中文', () => {
    setLocale('en')
    // 数据本身用拉丁文：这一条测的是界面自己的文案，不是别人填的内容。
    mount(
      payload({
        materials: [],
        teaching: {
          systemPrompt: 'Week {current_week}: loops.',
          currentWeek: 5,
          allowedTopics: ['Loops'],
          avoidInCode: ['Recursion'],
          source: 'task',
        },
      })
    )

    const text = document.body.textContent ?? ''
    expect(text).toContain(i18n.global.t('tasks.inheritance.title'))
    expect(/[一-鿿]/.test(text)).toBe(false)
  })
})
