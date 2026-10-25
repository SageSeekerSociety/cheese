// 回答里那张自动化卡片：写着它现在怎样，等着的能取消、开着的能暂停、暂停的能恢复；结束了的
// 什么都不能点；没做成就照旧，并说一声。
import type { AgentRuleNow, AgentRules } from '@/lib/agentRules'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const toastError = vi.fn()
vi.mock('vuetify-sonner', () => ({ toast: { error: (...a: unknown[]) => toastError(...a) } }))

import AssistantRuleCard from './AssistantRuleCard.vue'

import i18n, { setLocale } from '@/i18n'
import { agentRulesKey } from '@/lib/agentRules'

const RULE = { id: 'w1', when: '任务「接入搜索」提交审阅时', note: '读改动，对照任务要求列出问题' }

function mount(state: AgentRuleNow | null, act: AgentRules['act'] = vi.fn(async () => true)) {
  return render(AssistantRuleCard, {
    props: { rule: RULE },
    global: {
      plugins: [createVuetify({ components }), i18n],
      provide: { [agentRulesKey as symbol]: { now: () => state, act } },
    },
  })
}

describe('AssistantRuleCard', () => {
  beforeEach(() => {
    setLocale('zh-CN')
    toastError.mockReset()
  })

  it('a rule still waiting can be called off', async () => {
    const act = vi.fn(async () => true)
    const view = mount({ state: 'waiting' }, act)
    expect(view.getByText('等待中')).toBeTruthy()

    await fireEvent.click(view.getByRole('button', { name: '取消' }))

    expect(act).toHaveBeenCalledWith('w1', 'cancel')
  })

  it('a repeating rule that is on can be paused, a paused one resumed', async () => {
    expect(mount({ state: 'on' }).getByRole('button', { name: '暂停' })).toBeTruthy()
    expect(mount({ state: 'paused', why: 'credits' }).getByRole('button', { name: '恢复' })).toBeTruthy()
  })

  it('a rule that has ended offers nothing to do', () => {
    const view = mount({ state: 'gaveUp' })
    expect(view.getByText('14 天内没有发生，已停止等待')).toBeTruthy()
    expect(view.queryByRole('button')).toBeNull()
  })

  it('an action that did not go through says so', async () => {
    const view = mount(
      { state: 'waiting' },
      vi.fn(async () => false)
    )
    await fireEvent.click(view.getByRole('button', { name: '取消' }))
    await waitFor(() => expect(toastError).toHaveBeenCalledWith('操作失败'))
  })
})
