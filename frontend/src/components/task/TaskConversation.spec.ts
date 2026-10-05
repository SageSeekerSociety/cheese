/** 任务里只有负责人能和 AI 队友说话；别人看得到对话，但说话的地方换成回房间的入口。 */
import type { Component } from 'vue'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render as rawRender } from '@testing-library/vue'
import { describe, expect, it, vi } from 'vitest'

vi.mock('../../me', () => ({ myHandle: () => 'alice' }))

import TaskConversation from './TaskConversation.vue'

import { setLocale } from '@/i18n'

const vuetify = createVuetify({ components, directives })

setLocale('zh-CN')

function render(component: Component, options: { props: Record<string, unknown> }) {
  return rawRender(component, { props: options.props, global: { plugins: [vuetify] } })
}

const base = { blocks: [], draft: '', roomTitle: '前端', agentName: '芝士' }

describe('任务对话', () => {
  it('负责人能发出去', async () => {
    const { container, emitted } = render(TaskConversation, { props: { ...base, draft: '先看现状' } })
    expect(container.querySelector('[data-testid="task-composer"]')).not.toBeNull()
    await fireEvent.submit(container.querySelector('form') as HTMLFormElement)
    expect(emitted().send).toEqual([['先看现状']])
  })

  it('不是负责人：没有输入框，只有回到房间的入口', async () => {
    const { container, emitted } = render(TaskConversation, { props: { ...base, blocked: 'not-owner' } })
    expect(container.querySelector('[data-testid="task-composer"]')).toBeNull()
    await fireEvent.click(container.querySelector('[data-testid="task-blocked"] button') as HTMLElement)
    expect(emitted()['open-room']).toHaveLength(1)
  })

  it('任务关了：谁都不能再说话', () => {
    const { container } = render(TaskConversation, { props: { ...base, blocked: 'closed' } })
    expect(container.querySelector('[data-testid="task-composer"]')).toBeNull()
    expect(container.querySelector('[data-testid="task-blocked"] button')).toBeNull()
  })

  it('空草稿不发', async () => {
    const { container, emitted } = render(TaskConversation, { props: { ...base, draft: '   ' } })
    await fireEvent.submit(container.querySelector('form') as HTMLFormElement)
    expect(emitted().send).toBeUndefined()
  })
})
