// 「MCP 服务器」那一块的数据与动作：读一次、连接/断开/填密钥、授权回来的那一条回跳。
import type { McpServer, McpServerList } from '@/api'

import { defineComponent, h, nextTick, ref } from 'vue'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { cleanup, render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const getMcpServers = vi.fn()
const connectMcpServer = vi.fn()
const disconnectMcpServer = vi.fn()
const setMcpSecret = vi.fn()
const clearMcpSecret = vi.fn()
const claimMcpHandoff = vi.fn()

vi.mock('@/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api')>()
  return {
    ...actual,
    getMcpServers: (...a: unknown[]) => getMcpServers(...a),
    connectMcpServer: (...a: unknown[]) => connectMcpServer(...a),
    disconnectMcpServer: (...a: unknown[]) => disconnectMcpServer(...a),
    setMcpSecret: (...a: unknown[]) => setMcpSecret(...a),
    clearMcpSecret: (...a: unknown[]) => clearMcpSecret(...a),
    claimMcpHandoff: (...a: unknown[]) => claimMcpHandoff(...a),
  }
})

const goAuthorize = vi.hoisted(() => vi.fn(() => true))
vi.mock('@/lib/desktopApp', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/desktopApp')>()
  return { ...actual, goAuthorize }
})

// The confirm in front of connecting: answered as each test says.
const confirmed = vi.hoisted(() => ({ answer: true, later: null as Promise<boolean> | null }))
vi.mock('@/plugins/dialog', () => ({
  useDialog: () => ({
    confirm: () => ({ wait: () => confirmed.later ?? Promise.resolve(confirmed.answer) }),
  }),
}))

// 一次性动作的结果走全局 toast（§3.11）：这里只验它说了什么。
const sonner = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }))
vi.mock('vuetify-sonner', () => ({ toast: { success: sonner.success, error: sonner.error } }))

import { useProjectMcpSettings } from './useProjectMcpSettings'

const tracker: McpServer = {
  name: 'tracker',
  transport: 'http',
  host: 'mcp.example.test',
  command: null,
  run: null,
  auth: 'oauth',
  status: 'disconnected',
  declared_by: null,
  added: null,
  shadowed: false,
  authorized_by: null,
  authorized_at: null,
  variables: [],
}

const search: McpServer = {
  ...tracker,
  name: 'search',
  auth: 'headers',
  status: 'missing_values',
  variables: [{ name: 'SEARCH_KEY', set: false, updated_by: null, updated_at: null }],
}

const list: McpServerList = { servers: [tracker, search], problem: null }

const tick = () => new Promise<void>((r) => setTimeout(r, 0))

async function host(
  projectId: () => string = () => 'p',
  options?: Parameters<typeof useProjectMcpSettings>[1]
): Promise<{ state: ReturnType<typeof useProjectMcpSettings>; router: Router }> {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/projects/:projectId/settings', component: { template: '<div />' } }],
  })
  await router.push('/projects/p/settings')
  let state!: ReturnType<typeof useProjectMcpSettings>
  render(
    defineComponent({
      setup() {
        state = useProjectMcpSettings(projectId, options)
        return () => h('div')
      },
    }),
    { global: { plugins: [router] } }
  )
  return { state, router }
}

beforeEach(() => {
  getMcpServers.mockReset().mockResolvedValue(list)
  connectMcpServer.mockReset().mockResolvedValue({ authorization_url: 'https://as.example.test/authorize' })
  disconnectMcpServer.mockReset().mockResolvedValue(null)
  setMcpSecret.mockReset().mockResolvedValue(null)
  clearMcpSecret.mockReset().mockResolvedValue(null)
  goAuthorize.mockReset().mockReturnValue(true)
  sonner.success.mockReset()
  sonner.error.mockReset()
})
afterEach(cleanup)

describe('useProjectMcpSettings', () => {
  it('reads once on mount', async () => {
    const { state } = await host()
    expect(getMcpServers).toHaveBeenCalledWith('p')
    await tick()
    expect(state.list.value).toEqual(list)
  })

  it('connecting sends the browser to the authorization server', async () => {
    const { state } = await host()
    await tick()
    await state.connect(tracker)
    expect(connectMcpServer).toHaveBeenCalledWith('p', 'tracker')
    expect(goAuthorize).toHaveBeenCalledWith('https://as.example.test/authorize')
    expect(state.busy.value).toBe('')
  })

  it('cancelling the confirm leaves the connection unstarted', async () => {
    confirmed.answer = false
    const { state } = await host()
    await tick()
    await state.connect(tracker)
    expect(connectMcpServer).not.toHaveBeenCalled()
    expect(goAuthorize).not.toHaveBeenCalled()
    confirmed.answer = true
  })

  it('a confirm answered after switching projects connects nothing', async () => {
    const project = ref('a')
    let answer!: (yes: boolean) => void
    confirmed.later = new Promise<boolean>((resolve) => (answer = resolve))
    const { state } = await host(() => project.value)
    await tick()
    const asking = state.connect(tracker)
    project.value = 'b'
    answer(true)
    await asking
    confirmed.later = null
    expect(connectMcpServer).not.toHaveBeenCalled()
    expect(goAuthorize).not.toHaveBeenCalled()
  })

  it('disconnecting reloads the list', async () => {
    const { state } = await host()
    await tick()
    await state.disconnect(tracker)
    expect(disconnectMcpServer).toHaveBeenCalledWith('p', 'tracker')
    expect(getMcpServers).toHaveBeenCalledTimes(2)
  })

  it('sends a secret once, clears the draft and reloads', async () => {
    const { state } = await host()
    await tick()
    state.setValue('SEARCH_KEY', 'sk-123')
    await state.save('SEARCH_KEY')
    expect(setMcpSecret).toHaveBeenCalledWith('p', 'SEARCH_KEY', 'sk-123')
    expect(state.values.SEARCH_KEY).toBe('')
    expect(getMcpServers).toHaveBeenCalledTimes(2)
  })

  it('does not send a blank secret', async () => {
    const { state } = await host()
    await tick()
    state.setValue('SEARCH_KEY', '   ')
    await state.save('SEARCH_KEY')
    expect(setMcpSecret).not.toHaveBeenCalled()
  })

  it('clears a stored secret and reloads', async () => {
    const { state } = await host()
    await tick()
    await state.clear('SEARCH_KEY')
    expect(clearMcpSecret).toHaveBeenCalledWith('p', 'SEARCH_KEY')
    expect(getMcpServers).toHaveBeenCalledTimes(2)
  })

  it('says the one-off failure with a toast', async () => {
    connectMcpServer.mockRejectedValue(new Error('nope'))
    const { state } = await host()
    await tick()
    await state.connect(tracker)
    expect(sonner.error).toHaveBeenCalledWith('nope')
    expect(state.busy.value).toBe('')
  })

  it('keeps the reason when the list read fails', async () => {
    getMcpServers.mockRejectedValue(new Error('down'))
    const { state } = await host()
    await tick()
    expect(state.error.value).toBe('down')
  })

  it('says a successful authorization once and takes it off the address', async () => {
    const { state, router } = await host()
    await tick()
    await router.push('/projects/p/settings?mcp=tracker&mcp_result=ok')
    state.consumeCallback()
    expect(sonner.success).toHaveBeenCalledTimes(1)
    await tick()
    expect(router.currentRoute.value.query).toEqual({})
  })

  it('says a failed authorization once and takes it off the address', async () => {
    const { state, router } = await host()
    await tick()
    await router.push('/projects/p/settings?mcp=tracker&mcp_error=failed')
    state.consumeCallback()
    expect(sonner.error).toHaveBeenCalledTimes(1)
    await tick()
    expect(router.currentRoute.value.query).toEqual({})
  })

  // 回调不存东西，只留领取号：登录着的设置页去领，领回来的才是这次连接的结果。只有发起
  // 连接的成员领得到，别人点了授权链接，令牌也进不了这个项目。
  it('claims what the authorization server sent back, says it once and drops the claim from the address', async () => {
    claimMcpHandoff.mockReset().mockResolvedValue({ query: { mcp: 'tracker', mcp_result: 'connected' } })
    const { state, router } = await host()
    await tick()
    await router.push('/projects/p/settings?mcp=tracker&mcp_handoff=h1')
    await state.consumeCallback()
    expect(claimMcpHandoff).toHaveBeenCalledWith('h1')
    expect(sonner.success).toHaveBeenCalledTimes(1)
    await tick()
    expect(router.currentRoute.value.query).toEqual({})
  })

  it('a claim that is not ours is said as a failure, not a connection', async () => {
    claimMcpHandoff.mockReset().mockResolvedValue({ query: { mcp: 'tracker', mcp_error: 'mcpAuthLinkInvalid' } })
    const { state, router } = await host()
    await tick()
    await router.push('/projects/p/settings?mcp=tracker&mcp_handoff=h2')
    await state.consumeCallback()
    expect(sonner.error).toHaveBeenCalledTimes(1)
    expect(sonner.success).not.toHaveBeenCalled()
  })

  it('waits for enabled to turn on before reading', async () => {
    const on = ref(false)
    await host(() => 'p', { enabled: () => on.value })
    await tick()
    expect(getMcpServers).not.toHaveBeenCalled()
    on.value = true
    await nextTick()
    await tick()
    expect(getMcpServers).toHaveBeenCalledTimes(1)
  })
})
