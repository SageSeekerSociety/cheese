// 项目外壳（ProjectShell）一直挂着，换页只换路由：在频道或项目总览上问芝士，开新话题的请求
// 还在路上时人点开了一份文档，这一问挂的仍是问的那一页，不是后来打开的文档。
// 真的 ProjectShell 和 vue-router；开话题、问的请求在 fetch 这一层答着。
import { nextTick, reactive } from 'vue'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render, waitFor } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { afterEach, expect, it, vi } from 'vitest'

const state = vi.hoisted(() => ({ store: null as unknown }))
vi.mock('@/stores/workspace', () => ({ useWorkspaceStore: () => state.store }))
vi.mock('@/me', () => ({ myHandle: () => 'alice' }))
vi.mock('@/query/projectFeed', () => ({ useProjectFeed: () => {} }))

import ProjectShell from './ProjectShell.vue'

import i18n from '@/i18n'
import { agentContext, agentPageOff, askAgent, resetAgent } from '@/stores/agent'
import { seedProject } from '@/test/seedQueries'

const PROJECT = '11111111-1111-4111-8111-111111111111'
const ROOM = '22222222-2222-4222-8222-222222222222'
const DOC = '33333333-3333-4333-8333-333333333333'

afterEach(() => {
  cleanup()
  resetAgent()
  agentPageOff.value = false
  vi.unstubAllGlobals()
})

it.each(['room', 'project'])(
  'a question asked on a %s goes with it, though a document is opened before it goes out',
  async (from) => {
    seedProject(PROJECT, { topics: [], unread: {}, privateUnread: {}, notifyLevels: {} }, 'alice')
    state.store = reactive({
      projectName: 'Project',
      accessDenied: null,
      error: null,
      projectRefused: null,
      activeTopicId: null,
      activeDmPeer: null,
      openProject: vi.fn(),
      rememberPlace: vi.fn(),
    })
    let release = () => {}
    let conversationStarted = false
    const gate = new Promise<void>((resolve) => {
      release = resolve
    })
    const bodies: Record<string, unknown>[] = []
    vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
      const json = (data: unknown, status = 200) =>
        new Response(JSON.stringify({ code: 200, message: 'ok', data }), {
          status,
          headers: { 'content-type': 'application/json' },
        })
      if (url.endsWith('/assistant/conversations') && init?.method === 'POST') {
        conversationStarted = true
        await gate
        return json({ id: 'c1', title: '', lastActiveAt: '2026-10-27T03:00:00Z' }, 201)
      }
      if (url.endsWith('/assistant/conversations')) return json({ conversations: [] })
      if (url.endsWith('/ask')) {
        bodies.push(JSON.parse(String(init?.body)))
        return new Response('event: done\ndata: {}\n\n', { headers: { 'content-type': 'text/event-stream' } })
      }
      throw new Error(`Unexpected URL ${url}`)
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
            { path: 'channels/:topicId', component: Stub },
            { path: 'docs/:docId', component: Stub },
            { path: 'overview', component: Stub },
          ],
        },
      ],
    })
    const App = { components: { RouterView }, template: '<v-app><router-view /></v-app>' }
    render(App, { global: { plugins: [router, createPinia(), createVuetify({ components, directives }), i18n] } })
    await router.push(`/projects/${PROJECT}/${from === 'room' ? `channels/${ROOM}` : 'overview'}`)
    await nextTick()
    const expected = from === 'room' ? { kind: 'topic', id: ROOM } : { kind: 'project', id: PROJECT }
    expect(agentContext.value?.page).toEqual(expected)
    const question = askAgent('Summarize this page')
    await waitFor(() => expect(conversationStarted).toBe(true))
    await router.push(`/projects/${PROJECT}/docs/${DOC}`)
    expect(agentContext.value?.page).toEqual({ kind: 'doc', id: DOC })
    release()
    await question
    expect(bodies[0]).toMatchObject({ page: expected })
  }
)
