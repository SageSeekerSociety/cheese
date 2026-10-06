/** AI 队友只能提议任务，创建由人决定：一条提议给两个选择。 */
import type { Component } from 'vue'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render as rawRender } from '@testing-library/vue'
import { describe, expect, it } from 'vitest'

import TaskProposalCard from './TaskProposalCard.vue'

import { setLocale } from '@/i18n'

const vuetify = createVuetify({ components, directives })

setLocale('zh-CN')

function render(component: Component, options: { props: Record<string, unknown> }) {
  return rawRender(component, { props: options.props, global: { plugins: [vuetify] } })
}

describe('任务提议卡', () => {
  it('可以创建，也可以不用', async () => {
    const { getByText, emitted } = render(TaskProposalCard, {
      props: { proposal: { id: 'p1', title: '迁移旧数据', summary: '把旧表搬到新表' }, proposer: '芝士' },
    })
    await fireEvent.click(getByText('创建任务'))
    await fireEvent.click(getByText('不用'))
    expect(emitted().decide).toEqual([['accept'], ['dismiss']])
  })
})
