// 芝士起草、等人确认的一件事：确认前能改，点了才往外发（发由外面做），发出以后只写结果。
import type { PanelProposal } from '@/lib/assistantView'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { beforeEach, describe, expect, it } from 'vitest'

import AgentProposal from './AgentProposal.vue'

import i18n, { setLocale } from '@/i18n'

const MESSAGE: PanelProposal = { id: 3, kind: 'message', state: 'pending', where: '综合', text: '演示视频我来负责。' }
const TASK: PanelProposal = {
  id: 4,
  kind: 'task',
  state: 'pending',
  where: '综合',
  title: '排查路线规划超时',
  text: '复现超时，修好并补测试。',
}

function mount(proposal: PanelProposal) {
  return render(AgentProposal, {
    props: { proposal },
    global: { plugins: [createVuetify({ components, directives }), i18n] },
  })
}

beforeEach(() => setLocale('zh-CN'))

describe('AgentProposal', () => {
  it('a drafted message waits for the person, who may change it before it is sent', async () => {
    const view = mount(MESSAGE)
    expect(view.getByText('待你确认')).toBeTruthy()
    expect(view.getByText('综合')).toBeTruthy()

    await fireEvent.update(view.getByLabelText('内容'), '演示视频我来负责，周五前给初版。')
    await fireEvent.click(view.getByRole('button', { name: '发送' }))

    expect(view.emitted('send')).toEqual([[3, { text: '演示视频我来负责，周五前给初版。' }]])
  })

  it('a drafted task is opened with its title and brief, and cannot go without a title', async () => {
    const view = mount(TASK)
    const open = view.getByRole('button', { name: '开任务' })

    await fireEvent.update(view.getByLabelText('标题'), '  ')
    expect((open as HTMLButtonElement).disabled).toBe(true)
    await fireEvent.update(view.getByLabelText('标题'), '排查超时')
    await fireEvent.click(open)

    expect(view.emitted('send')).toEqual([[4, { title: '排查超时', text: '复现超时，修好并补测试。' }]])
  })

  it('can be put aside', async () => {
    const view = mount(MESSAGE)
    await fireEvent.click(view.getByRole('button', { name: '不用了' }))
    expect(view.emitted('dismiss')).toEqual([[3]])
  })

  it('once sent it says so, and a task it opened can be visited', async () => {
    const view = mount({ ...TASK, state: 'sent', task: 't-9' })
    expect(view.getByText('已开任务「排查路线规划超时」')).toBeTruthy()
    expect(view.queryByRole('button', { name: '开任务' })).toBeNull()

    await fireEvent.click(view.getByRole('button', { name: '查看' }))

    expect(view.emitted('open')).toEqual([['t-9']])
  })

  it('another action says what it does and why it was asked, and is confirmed as it is', async () => {
    const view = mount({
      id: 5,
      kind: 'act',
      state: 'pending',
      where: '',
      text: '把 bob 移出频道「综合」',
      reason: '你设了「移除成员」要先问你',
    })
    expect(view.getByText('把 bob 移出频道「综合」')).toBeTruthy()
    expect(view.getByText('你设了「移除成员」要先问你')).toBeTruthy()

    await fireEvent.click(view.getByRole('button', { name: '确认' }))

    expect(view.emitted('send')).toEqual([[5, { text: '把 bob 移出频道「综合」' }]])
  })
})
