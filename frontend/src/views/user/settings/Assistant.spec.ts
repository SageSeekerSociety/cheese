// 「芝士」：勾选「始终需要确认」的一类就存一次；两段补充说明点「保存」才存；最近的操作
// 写已执行还是已请你确认，请你确认的写原因。
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import Assistant from './Assistant.vue'

import { setLocale } from '@/i18n'

const { rulesMock, saveMock, activityMock } = vi.hoisted(() => ({
  rulesMock: vi.fn(),
  saveMock: vi.fn(),
  activityMock: vi.fn(),
}))

vi.mock('@/api', () => ({
  getAssistantRules: rulesMock,
  saveAssistantRules: saveMock,
  getAssistantActivity: activityMock,
}))

const CATEGORIES = [
  { code: 'remove_member', label: '移除成员' },
  { code: 'to_everyone', label: '在综合频道发消息' },
]

function mount() {
  return render(Assistant, { global: { plugins: [createVuetify({ components, directives })] } })
}

beforeEach(() => {
  setLocale('zh-CN')
  rulesMock.mockResolvedValue({ ask: ['remove_member'], allowText: '', askText: '', categories: CATEGORIES })
  saveMock.mockImplementation(async (r) => ({ ...r, categories: CATEGORIES }))
  activityMock.mockResolvedValue({
    items: [
      {
        id: 2,
        kind: 'member',
        summary: '将 bob 移出频道「综合」（项目「P」）',
        verdict: 'asked',
        stage: 'rule',
        reason: '你设置了「移除成员」需要确认',
        at: new Date().toISOString(),
      },
      {
        id: 1,
        kind: 'message',
        summary: '在频道「综合」（项目「P」）发消息：\n收到',
        verdict: 'allowed',
        stage: 'quick',
        reason: '',
        at: new Date().toISOString(),
      },
    ],
  })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('芝士 settings', () => {
  it('lists recent actions, saying which were confirmed by the person and why', async () => {
    const view = mount()
    await waitFor(() => expect(view.getByText('最近的操作')).toBeTruthy())
    expect(view.getByText('已请你确认')).toBeTruthy()
    expect(view.getByText('原因：你设置了「移除成员」需要确认')).toBeTruthy()
    expect(view.getByText('已执行')).toBeTruthy()
  })

  it('ticking a kind saves at once; the notes save when asked', async () => {
    const view = mount()
    await waitFor(() => expect(view.getByLabelText('在综合频道发消息')).toBeTruthy())

    await fireEvent.change(view.getByRole('checkbox', { name: '在综合频道发消息' }))
    await waitFor(() =>
      expect(saveMock).toHaveBeenCalledWith({ ask: ['remove_member', 'to_everyone'], allowText: '', askText: '' })
    )

    await fireEvent.update(view.getByLabelText('需要确认'), '发给老师的消息')
    await fireEvent.click(view.getByRole('button', { name: '保存' }))
    await waitFor(() =>
      expect(saveMock).toHaveBeenLastCalledWith({
        ask: ['remove_member', 'to_everyone'],
        allowText: '',
        askText: '发给老师的消息',
      })
    )
  })
})
