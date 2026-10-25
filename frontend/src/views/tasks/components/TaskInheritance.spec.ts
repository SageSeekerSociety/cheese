// 「会继承什么」这一块 (#944)：屏幕上那几项**就是**接口给的那几个值。
//
// 后端的另一半在 `backend/tests/integration/test_task_inheritance.py`：那边断言
// 接口给的 `teaching` 等于 `protocol.resolve()` 的合成结果，并且去掉一层它跟着
// 变。这里接住前半段 —— 组件不自己算、不自己拼，渲染出来的就是那份数据。两段合
// 起来才是「界面数据 = 合成结果」。
import type { TaskInheritanceData } from '@/types/taskBoard'

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
      mode: 'guide',
      systemPrompt: '本题只用循环，不用递归。',
      spacePrompt: '先问学生试过什么。',
      materialIds: [],
      documentIds: [],
      source: 'both',
    },
    agent: null,
    packPrompt: null,
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
    expect(text).toContain(i18n.global.t('spaces.teaching.mode.guide'))
    // 两边都写了要求：分开摆，各有标题。
    expect(text).toContain(i18n.global.t('tasks.inheritance.spacePrompt'))
    expect(text).toContain('先问学生试过什么。')
    expect(text).toContain(i18n.global.t('tasks.inheritance.taskPrompt'))
    expect(text).toContain('本题只用循环，不用递归。')
    // 来源单独说一句，不是一个只有结果的字符串。
    expect(text).toContain(i18n.global.t('tasks.inheritance.from.both'))
  })

  it('来源跟着数据走：只有空间的就说空间', () => {
    setLocale('zh-CN')
    mount(payload({ teaching: { mode: 'example', systemPrompt: null, source: 'space' } }))

    const text = document.body.textContent ?? ''
    expect(text).toContain(i18n.global.t('tasks.inheritance.from.space'))
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

  it('都没说时不编一句话：没有辅导、没有来源', () => {
    setLocale('zh-CN')
    mount(
      payload({
        resourcePack: {},
        teaching: { mode: null, systemPrompt: null, source: null },
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
        teaching: { mode: 'spec', systemPrompt: 'Loops only.', source: 'task' },
      })
    )

    const text = document.body.textContent ?? ''
    expect(text).toContain(i18n.global.t('tasks.inheritance.title'))
    expect(/[一-鿿]/.test(text)).toBe(false)
  })

  it('默认队友：报出名字与它会带的技能', () => {
    setLocale('zh-CN')
    mount(
      payload({
        agent: { typeName: 'academic-research', displayName: '学术研究', skills: ['documents', 'wolfram'] },
      })
    )

    const text = document.body.textContent ?? ''
    expect(text).toContain(i18n.global.t('tasks.inheritance.agent'))
    expect(text).toContain('学术研究')
    expect(text).toContain(i18n.global.t('tasks.inheritance.agentSkills'))
    expect(text).toContain('documents')
    expect(text).toContain('wolfram')
  })

  it('没指定默认队友时也说一句，不整行消失', () => {
    setLocale('zh-CN')
    mount(payload({ agent: null }))

    const text = document.body.textContent ?? ''
    expect(text).toContain(i18n.global.t('tasks.inheritance.agentNone'))
    expect(text).not.toContain(i18n.global.t('tasks.inheritance.agentSkills'))
  })

  it('资源包带的那段 prompt 说明它会加给默认队友', () => {
    setLocale('zh-CN')
    mount(payload({ packPrompt: '先写测试再写实现' }))

    const text = document.body.textContent ?? ''
    expect(text).toContain(i18n.global.t('tasks.inheritance.packPrompt'))
    expect(text).toContain('先写测试再写实现')
  })
})
