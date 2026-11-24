// 领题或申请没成：确认框留着，填过的联系方式还在，框里说出原因；成了才关框。
//
// 确认框（`TaskDialogs`）和发请求的那一半（`useTaskParticipation`）隔着事件总线，题目页
// （`Detail.vue`）把 `submit-verify` 接到 `handleVerifyInfoSubmit` 上。这里照题目页那样
// 接好两边，只把接口换掉。
import { defineComponent, h, ref } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

const join = vi.fn()
vi.mock('@/api/taskBoard', () => ({ TaskBoardApi: { join: (...a: unknown[]) => join(...a) } }))
vi.mock('../composables/useTaskInheritance', () => ({
  useTaskInheritance: () => ({ inheritance: ref(null), loading: ref(false) }),
}))
vi.mock('vuetify-sonner', () => ({ toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() } }))

import { useTaskParticipation } from '../composables/useTaskParticipation'
import { useEvents } from '../events'

import TaskDialogs from './TaskDialogs.vue'

import { ApiError } from '@/api/http'
import i18n, { setLocale } from '@/i18n'
import AccountService from '@/services/account'

const t = (key: string) => i18n.global.t(key)

const TASK = {
  id: 42,
  name: '题',
  startMode: 'DIRECT',
  submitterType: 'USER',
  requireRealName: false,
  mentors: [],
}

const Page = defineComponent({
  setup() {
    const taskData = ref(TASK)
    const participation = useTaskParticipation({ taskData, loadTaskData: async () => {} } as never)
    useEvents().on('submit-verify', (data) => void participation.handleVerifyInfoSubmit(data))
    return () =>
      h(TaskDialogs, {
        taskData: TASK as never,
        availableTeams: [],
        loadingTeams: false,
        joinedTeams: [],
        selectedLeaveTeamId: null,
        participationInfo: { identities: [] },
      })
  },
})

function mountPage() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: { render: () => null } },
      { path: '/projects/:id', component: { render: () => null } },
    ],
  })
  render(Page, {
    global: {
      plugins: [createVuetify({ components, directives }), router, i18n],
      stubs: {
        AdaptiveDialog: {
          props: ['modelValue', 'title', 'primaryLabel'],
          emits: ['primary'],
          template:
            '<div v-if="modelValue"><h2>{{ title }}</h2><slot /><button @click="$emit(\'primary\')">{{ primaryLabel }}</button></div>',
        },
      },
    },
  })
  return router
}

async function fillAndConfirm() {
  useEvents().emit('verify-dialog-open', true)
  await waitFor(() => expect(document.querySelector('input[name="phone"]')).toBeTruthy())
  await fireEvent.update(document.querySelector('input[name="phone"]')!, '13800000000')
  await fireEvent.click(screen.getByRole('button', { name: t('tasks.verifyDialog.confirmStart') }))
}

/** 页面上所有提醒的字（表单的每一栏也各有一个空的提醒位置）。 */
const alerts = () =>
  screen
    .getAllByRole('alert')
    .map((a) => a.textContent ?? '')
    .join('\n')

const dialogOpen = () => document.querySelector('h2')?.textContent === t('tasks.verifyDialog.titleStart')

describe('开始做这道题', () => {
  beforeAll(() => setLocale('zh-CN'))
  beforeEach(() => {
    AccountService.user = { id: 7, username: 'stu' } as never
    join.mockReset()
  })
  afterEach(() => {
    cleanup()
    AccountService.user = null
  })

  it('服务器没收下：确认框留着，联系方式还在，框里说出原因', async () => {
    join.mockRejectedValue(new ApiError(409, '这道题的名额已满', 'TaskFullError'))
    mountPage()

    await fillAndConfirm()

    await waitFor(() => expect(alerts()).toContain('这道题的名额已满'))
    expect(dialogOpen()).toBe(true)
    expect((document.querySelector('input[name="phone"]') as HTMLInputElement).value).toBe('13800000000')
  })

  it('成了：关框，进开出来的项目', async () => {
    join.mockResolvedValue({ participant: { id: 1 }, project: { id: 'p-9' } })
    const router = mountPage()

    await fillAndConfirm()

    await waitFor(() => expect(router.currentRoute.value.fullPath).toBe('/projects/p-9'))
    expect(dialogOpen()).toBe(false)
  })
})
