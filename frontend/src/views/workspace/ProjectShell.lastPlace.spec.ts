/**
 * rail 上那个项目格子点回来时要落回上次待的地方，而「上次待的地方」正是在这里记下
 * 的。在一个任务页上，记的必须是那件任务本身——只记它所在的频道，点回来就掉回频道，
 * 找不到刚才那件任务。频道也一起记着，任务页画不出来时退回它。
 *
 * 任务页自己怎么画在别处测；这里驱动真实路由和 ProjectShell，只看它记了什么。
 */
import type { Topic } from '@/cx_types'

import { reactive } from 'vue'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render, waitFor } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { describe, expect, it, vi } from 'vitest'

/** ProjectShell 会读的那几样；这里只有 rememberPlace 是要看的。 */
type Store = {
  projectName: string
  accessDenied: null
  error: null
  projectRefused: null
  activeTopicId: string | null
  activeDmPeer: string | null
  openProject: ReturnType<typeof vi.fn>
  rememberPlace: ReturnType<typeof vi.fn>
}

const store = vi.hoisted(() => ({ value: null as unknown as Store }))
vi.mock('@/stores/workspace', () => ({ useWorkspaceStore: () => store.value }))
vi.mock('@/me', () => ({ myHandle: () => 'alice' }))
// 项目推送是另一条连接，这里不看它。
vi.mock('@/query/projectFeed', () => ({ useProjectFeed: () => {} }))

import ProjectShell from './ProjectShell.vue'

import i18n from '@/i18n'
import { rememberThing } from '@/lib/addresses'
import { seedProject } from '@/test/seedQueries'

const TOPIC = { id: 't9', project_id: 'p1', title: 'Room', kind: 'topic', status: 'active' } as Topic

function setup() {
  // 项目框每 30 秒问一次的那几份：刚读过，这一刻不问。
  seedProject('p1', { topics: [TOPIC], unread: {}, privateUnread: {}, notifyLevels: {} }, 'alice')
  store.value = reactive({
    projectName: 'Project',
    accessDenied: null,
    error: null,
    projectRefused: null,
    activeTopicId: null,
    activeDmPeer: null,
    openProject: vi.fn(),
    rememberPlace: vi.fn(),
  })
  const Stub = { name: 'Stub', template: '<div />' }
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      {
        path: '/projects/:projectId',
        component: ProjectShell,
        props: true,
        children: [
          { name: 'workspace-topic', path: 'channels/:topicId', component: Stub, props: true },
          { name: 'workspace-task', path: 'tasks/:taskId', component: Stub, props: true },
        ],
      },
    ],
  })
  const App = { components: { RouterView }, template: '<v-app><router-view /></v-app>' }
  render(App, { global: { plugins: [router, createPinia(), createVuetify({ components, directives }), i18n] } })
  return { router }
}

describe('the place a project remembers', () => {
  it('remembers the task being looked at, with the room it sits in', async () => {
    rememberThing({ project_id: 'p1', slug: 'p1', kind: 'tasks', id: 'task-7', room_id: 't9', number: 7 })
    const { router } = setup()

    await router.push('/projects/p1/tasks/task-7')

    await waitFor(() =>
      expect(store.value.rememberPlace).toHaveBeenCalledWith('p1', { kind: 'task', id: 'task-7', room: 't9' })
    )
  })

  it('remembers the channel itself when that is what is open', async () => {
    const { router } = setup()

    await router.push('/projects/p1/channels/t9')

    await waitFor(() =>
      expect(store.value.rememberPlace).toHaveBeenCalledWith('p1', { kind: 'channel', id: 't9', room: 't9' })
    )
  })
})
