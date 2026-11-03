// 「芝士」：勾选一类、添加或删除一条都立即保存；例行事务列出何时运行、最近一次的结果，
// 暂停立即生效，删除先确认。
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import Assistant from './Assistant.vue'

import i18n, { setLocale } from '@/i18n'

const {
  rulesMock,
  saveMock,
  activityMock,
  routinesMock,
  setRoutineMock,
  memoryMock,
  editMemoryMock,
  deleteMemoryMock,
} = vi.hoisted(() => ({
  rulesMock: vi.fn(),
  saveMock: vi.fn(),
  activityMock: vi.fn(),
  routinesMock: vi.fn(),
  setRoutineMock: vi.fn(),
  memoryMock: vi.fn(),
  editMemoryMock: vi.fn(),
  deleteMemoryMock: vi.fn(),
}))

vi.mock('@/api', () => ({
  getAssistantRules: rulesMock,
  saveAssistantRules: saveMock,
  getAssistantActivity: activityMock,
  getAssistantRoutines: routinesMock,
  setAssistantRoutine: setRoutineMock,
  getAssistantMemory: memoryMock,
  saveAssistantMemoryItem: editMemoryMock,
  deleteAssistantMemoryItem: deleteMemoryMock,
  setAssistantMemoryEnabled: vi.fn(),
  clearAssistantMemory: vi.fn(),
}))

const MEMORY = {
  name: 'formal-to-teachers',
  description: '给老师写东西用书面语',
  type: 'feedback',
  text: '不用「哈」「呀」。',
  updatedAt: new Date().toISOString(),
}

vi.mock('@/stores/agent', () => ({ openAgentConversation: vi.fn() }))

const CATEGORIES = [
  { code: 'remove_member', label: '移除成员' },
  { code: 'to_everyone', label: '发给所有人的消息' },
]

const ROUTINE = {
  id: 'r1',
  title: '每日待办',
  note: '列出待办',
  when: '每天 08:00（Asia/Shanghai）',
  state: 'active',
  paused: '',
  nextRunAt: new Date(Date.now() + 3600_000).toISOString(),
  once: false,
  conversationId: null,
  lastRun: { status: 'done', at: new Date().toISOString(), asked: 2, conversationId: 'c1' },
  weekCredits: 3.2,
}

// The dialog itself is `ConfirmDialog`'s to test; here, only what confirming it does.
const ConfirmStub = {
  props: ['modelValue', 'title', 'confirmLabel'],
  emits: ['confirm'],
  template:
    '<div v-if="modelValue"><p>{{ title }}</p><button @click="$emit(\'confirm\')">{{ confirmLabel }}</button></div>',
}

function mount() {
  return render(Assistant, {
    global: { plugins: [createVuetify({ components, directives }), i18n], stubs: { ConfirmDialog: ConfirmStub } },
  })
}

beforeEach(() => {
  setLocale('zh-CN')
  rulesMock.mockResolvedValue({
    ask: ['remove_member', 'to_everyone'],
    askItems: ['发给老师的消息'],
    allowItems: [],
    categories: CATEGORIES,
  })
  saveMock.mockImplementation(async (r) => ({ ...r, categories: CATEGORIES }))
  routinesMock.mockResolvedValue({ routines: [ROUTINE], limit: 20 })
  memoryMock.mockResolvedValue({ enabled: true, items: [MEMORY] })
  editMemoryMock.mockImplementation(async (name: string, description: string, text: string) => ({
    enabled: true,
    items: [{ ...MEMORY, name, description, text }],
  }))
  deleteMemoryMock.mockResolvedValue({ enabled: true, items: [] })
  setRoutineMock.mockImplementation(async (_id: string, action: string) => ({
    routines: action === 'delete' ? [] : [{ ...ROUTINE, state: 'paused', paused: 'person' }],
    limit: 20,
  }))
  activityMock.mockResolvedValue({
    items: [
      {
        id: 2,
        kind: 'message',
        summary: '在频道「综合」（项目「P」）发消息：\n收到',
        verdict: 'asked',
        stage: 'rule',
        reason: '你设置了「发给所有人的消息」需要确认',
        at: new Date().toISOString(),
      },
      {
        id: 1,
        kind: 'channel',
        summary: '在项目「P」新建频道「论文」',
        verdict: 'allowed',
        stage: 'low',
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
  it('adding and deleting an entry each save at once', async () => {
    const view = mount()
    await waitFor(() => expect(view.getByText('发给老师的消息')).toBeTruthy())

    const allow = view.getByRole('textbox', { name: '无需确认' })
    await fireEvent.update(allow, '在我负责的频道里回答问题')
    await fireEvent.submit(allow.closest('form') as HTMLFormElement)
    await waitFor(() =>
      expect(saveMock).toHaveBeenLastCalledWith({
        ask: ['remove_member', 'to_everyone'],
        askItems: ['发给老师的消息'],
        allowItems: ['在我负责的频道里回答问题'],
      })
    )

    const [removeAsk] = view.getAllByRole('button', { name: '删除' })
    await fireEvent.click(removeAsk)
    await waitFor(() => expect(saveMock).toHaveBeenLastCalledWith(expect.objectContaining({ askItems: [] })))
  })

  it('lists routines with when they run and how the last run went; deleting asks first', async () => {
    const view = mount()
    await waitFor(() => expect(view.getByText('每天 08:00（Asia/Shanghai）')).toBeTruthy())
    expect(view.getByText('列出待办')).toBeTruthy()
    expect(view.getByText('最近一次：2 项操作待你确认')).toBeTruthy()
    expect(view.getByText('近 7 天 3.2 点')).toBeTruthy()

    await fireEvent.click(view.getByRole('button', { name: '暂停' }))
    await waitFor(() => expect(view.getByText('已暂停')).toBeTruthy())
    expect(setRoutineMock).toHaveBeenCalledWith('r1', 'pause')

    const deletes = view.getAllByRole('button', { name: '删除' })
    await fireEvent.click(deletes[deletes.length - 1])
    expect(setRoutineMock).not.toHaveBeenCalledWith('r1', 'delete')
    await waitFor(() => expect(view.getByText('删除自动化「每日待办」？')).toBeTruthy())
    const confirm = view.getAllByRole('button', { name: '删除' })
    await fireEvent.click(confirm[confirm.length - 1])
    await waitFor(() => expect(setRoutineMock).toHaveBeenCalledWith('r1', 'delete'))
    await waitFor(() => expect(view.getByText('暂无自动化')).toBeTruthy())
  })

  it('what 芝士 remembers is edited and deleted from the page', async () => {
    const view = mount()
    await waitFor(() => expect(view.getByText('给老师写东西用书面语')).toBeTruthy())
    expect(view.getByText('1 条')).toBeTruthy()

    await fireEvent.click(view.getByRole('button', { name: '编辑' }))
    const description = view.getByRole('textbox', { name: '描述' })
    await fireEvent.update(description, '给老师写东西要正式')
    await fireEvent.submit(description.closest('form') as HTMLFormElement)
    await waitFor(() =>
      expect(editMemoryMock).toHaveBeenCalledWith('formal-to-teachers', '给老师写东西要正式', '不用「哈」「呀」。')
    )
    await waitFor(() => expect(view.getByText('给老师写东西要正式')).toBeTruthy())

    const [removeMemory] = view
      .getAllByRole('button', { name: '删除' })
      .filter((b) => b.closest('li')?.textContent?.includes('给老师写东西要正式'))
    await fireEvent.click(removeMemory)
    await waitFor(() => expect(view.getByText('暂无记忆')).toBeTruthy())
    expect(deleteMemoryMock).toHaveBeenCalledWith('formal-to-teachers')
  })

  it('a one-off waits with a way to call it off, and once it has run it leaves the list', async () => {
    routinesMock.mockResolvedValue({
      routines: [
        { ...ROUTINE, id: 'w', when: '任务「接入搜索」提交审阅时', once: true, nextRunAt: null, lastRun: null },
        {
          ...ROUTINE,
          id: 'r2',
          when: '10月22日 17:00',
          note: '提醒交报告',
          once: true,
          state: 'done',
          nextRunAt: null,
        },
      ],
      limit: 20,
    })
    const view = mount()
    await waitFor(() => expect(view.getByText('任务「接入搜索」提交审阅时')).toBeTruthy())
    expect(view.getByText('等待中')).toBeTruthy()
    expect(view.queryByText('提醒交报告')).toBeNull()
    expect(view.queryByRole('button', { name: '暂停' })).toBeNull()

    await fireEvent.click(view.getByRole('button', { name: '取消' }))
    await waitFor(() => expect(setRoutineMock).toHaveBeenCalledWith('w', 'delete'))
  })
})
