// 「MCP 服务器」那一块的数据与动作（#1909）。
//
// 拆自 `components/ProjectMcpSettings.vue`：那件现在只吃 props、只往上发事件
// （`frontend_grade.py` 的 A 级），取数、连接/断开/填密钥、以及授权回来的那一条回跳都
// 落在这里，页面接线。和 `useBranchProtection.ts` 一个路子（`.claude/rules/architecture.md`
// 「组件不取数」）。
//
// 密钥输入框里那几行的草稿（`values`）留在这一层：填好一条密钥发出去、成了之后要把那
// 一行清空（原文不再留在屏幕上），而「成了」只有发请求的这一层知道。组件按 variable
// 的名字报上来、画回去。
//
// 连接走授权服务器的网页，回来时落在设置页，结果在地址栏的 `mcp_result` / `mcp_error`
// 里。`mcp_error` 是 apiError 里那句话的 key（参数在 `mcp_error_params`，JSON），这里按
// 读者的语言说出来，再由页面把它从地址栏拿掉 —— 刷新不会再说一遍。
//
// `enabled`：设置页一次只画一栏，这一栏没被打开过就不读，授权回跳也等这一刻才认。
import type { McpServer, McpServerDraft, McpServerList } from '@/api'

import { computed, onMounted, reactive, ref, watch } from 'vue'

import { useNavigation } from '@/composables/useNavigation'
import { holdRevealUntil } from '@/composables/useRevealGate'

import {
  addMcpServer,
  claimMcpHandoff,
  clearMcpSecret,
  connectMcpServer,
  disconnectMcpServer,
  getMcpServers,
  removeMcpServer,
  setMcpSecret,
  updateMcpServer,
} from '@/api'
import { t } from '@/i18n'
import { goAuthorize } from '@/lib/desktopApp'
import { renderNoticeMessage } from '@/lib/noticeText'
import { toast } from '@/lib/toast'
import { useDialog } from '@/plugins/dialog'

export interface UseProjectMcpSettingsOptions {
  /** 项目给它的 AI 队友起的名字，确认框里用。 */
  agentName?: () => string
  /** 这一栏此刻在不在屏幕上：第一次为真才读。不给就是挂上即读。 */
  enabled?: () => boolean
  revealGate?: { hold: () => () => void }
}

export function useProjectMcpSettings(projectId: () => string, options: UseProjectMcpSettingsOptions = {}) {
  const enabled = options.enabled ?? (() => true)
  const nav = useNavigation()
  const list = ref<McpServerList | null>(null)
  const error = ref('')
  const busy = ref('')
  const values = reactive<Record<string, string>>({})
  // 第一次取数有没有落定：显示闸靠它开，见 useRevealGate。
  const settled = ref(false)
  let generation = 0
  let actionGeneration = 0
  const dialog = useDialog()
  const agentName = options.agentName ?? (() => t('shell.agentDefaultName'))
  // 「添加 / 编辑」那张表：开没开、改的是哪一条（添加时 null）、存到一半、存失败的那句话。
  const form = reactive({ open: false, editing: null as McpServer | null, saving: false, error: '' })

  async function load() {
    settled.value = false
    const current = ++generation
    error.value = ''
    try {
      const result = await getMcpServers(projectId())
      if (current === generation) list.value = result
    } catch (e) {
      if (current === generation) error.value = e instanceof Error ? e.message : t('work.mcp.loadFailed')
    } finally {
      if (current === generation) settled.value = true
    }
  }

  // 连接、断开、填密钥这些都是一次性动作：结果跟这一次点击走，用全局 toast 说一声。
  function fail(e: unknown, fallback: string) {
    toast.error(e instanceof Error ? e.message : fallback)
  }

  /** 组件把某一行的输入报上来（每敲一下一次）。 */
  function setValue(name: string, value: string) {
    values[name] = value
  }

  async function connect(server: McpServer) {
    // 连上的是一个账号，项目里所有人都会通过 AI 队友用它：点下去之前说清楚。确认框开着
    // 的时候换了项目，答的就不是这个项目的了：记下问的是哪个项目，回来对一下。
    const project = projectId()
    const confirmed = await dialog
      .confirm(t('work.mcp.confirm.connectBody', { name: server.name, agent: agentName() }), {
        title: t('work.mcp.confirm.connectTitle', { name: server.name }),
        confirmLabel: t('work.mcp.confirm.connect'),
      })
      .wait()
      .catch(() => false)
    if (!confirmed || projectId() !== project) return
    const current = ++actionGeneration
    busy.value = server.name
    try {
      const { authorization_url } = await connectMcpServer(project, server.name)
      if (current === actionGeneration && goAuthorize(authorization_url)) busy.value = ''
    } catch (e) {
      if (current === actionGeneration) {
        fail(e, t('work.mcp.connectFailed'))
        busy.value = ''
      }
    }
  }

  async function disconnect(server: McpServer) {
    const current = ++actionGeneration
    busy.value = server.name
    try {
      await disconnectMcpServer(projectId(), server.name)
      if (current === actionGeneration) await load()
    } catch (e) {
      if (current === actionGeneration) fail(e, t('work.mcp.disconnectFailed'))
    } finally {
      if (current === actionGeneration) busy.value = ''
    }
  }

  async function save(name: string) {
    const value = values[name]?.trim()
    if (!value) return
    const current = ++actionGeneration
    busy.value = name
    try {
      await setMcpSecret(projectId(), name, value)
      if (current !== actionGeneration) return
      values[name] = ''
      await load()
    } catch (e) {
      if (current === actionGeneration) fail(e, t('work.mcp.saveFailed'))
    } finally {
      if (current === actionGeneration) busy.value = ''
    }
  }

  async function clear(name: string) {
    const current = ++actionGeneration
    busy.value = name
    try {
      await clearMcpSecret(projectId(), name)
      if (current === actionGeneration) await load()
    } catch (e) {
      if (current === actionGeneration) fail(e, t('work.mcp.saveFailed'))
    } finally {
      if (current === actionGeneration) busy.value = ''
    }
  }

  function openAdd() {
    Object.assign(form, { open: true, editing: null, saving: false, error: '' })
  }

  function openEdit(server: McpServer) {
    Object.assign(form, { open: true, editing: server, saving: false, error: '' })
  }

  /** 编辑时交给那张表的现状：设置里加的那一条，远程的或命令。 */
  const editing = computed<McpServerDraft | null>(() => {
    const server = form.editing
    if (!server?.added) return null
    if (server.command !== null)
      return { kind: 'command', name: server.name, command: server.command, env: server.added.env ?? {} }
    return {
      kind: 'remote',
      name: server.name,
      url: server.added.url ?? '',
      auth: server.auth,
      header: server.added.header,
      value: null,
    }
  })

  async function submit(draft: McpServerDraft) {
    form.saving = true
    form.error = ''
    try {
      await (form.editing ? updateMcpServer(projectId(), draft) : addMcpServer(projectId(), draft))
      form.open = false
      await load()
    } catch (e) {
      form.error = e instanceof Error ? e.message : t('work.mcp.saveFailed')
    } finally {
      form.saving = false
    }
  }

  async function remove(server: McpServer) {
    const project = projectId()
    const confirmed = await dialog
      .confirm(t('work.mcp.confirm.removeBody', { agent: agentName() }), {
        title: t('work.mcp.confirm.removeTitle', { name: server.name }),
        confirmLabel: t('work.mcp.confirm.remove'),
        danger: true,
      })
      .wait()
      .catch(() => false)
    if (!confirmed || projectId() !== project) return
    const current = ++actionGeneration
    busy.value = server.name
    try {
      await removeMcpServer(project, server.name)
      if (current === actionGeneration) await load()
    } catch (e) {
      if (current === actionGeneration) fail(e, t('work.mcp.removeFailed'))
    } finally {
      if (current === actionGeneration) busy.value = ''
    }
  }

  function callbackParams(raw: unknown): Record<string, unknown> {
    try {
      const parsed: unknown = typeof raw === 'string' ? JSON.parse(raw) : null
      return parsed && typeof parsed === 'object' ? (parsed as Record<string, unknown>) : {}
    } catch {
      return {}
    }
  }

  function failureText(name: string, failure: unknown, params: unknown): string {
    // 不认识的 code（或者没有 key 的那种拒绝，服务端发 failed）只说连接失败，不把 code 念出来。
    const reason = renderNoticeMessage({ key: String(failure), params: callbackParams(params) }, '')
    return reason ? t('work.mcp.connectFailedWith', { name, reason }) : t('work.mcp.connectFailedNamed', { name })
  }

  /** 从授权服务器回来：把结果说一次，再从地址栏拿掉，刷新不会再说一遍。
   *
   *  回调不存东西（`mcp_handoff`）：这一页登录着去领，领到的才是这次连接的结果。只有
   *  发起连接的成员领得到 —— 别人点了同一条授权链接，令牌也进不了这个项目。 */
  async function consumeCallback() {
    const route = nav?.route
    if (!route) return
    const { mcp_handoff: handoff, ...landed } = route.query
    let query: Record<string, unknown> = landed
    if (typeof handoff === 'string' && handoff) {
      try {
        query = { ...landed, ...(await claimMcpHandoff(projectId(), handoff)).query }
      } catch {
        query = { ...landed, mcp_error: 'failed' }
      }
    }
    const { mcp, mcp_result: result, mcp_error: failure, mcp_error_params: failureParams, ...rest } = query
    if (!result && !failure) {
      if (handoff) void nav?.navigate({ query: rest as typeof route.query, hash: route.hash }, { replace: true })
      return
    }
    const name = String(mcp ?? '')
    if (failure) toast.error(failureText(name, failure, failureParams))
    else toast.success(t('work.mcp.connectedNotice', { name }))
    // 换掉地址、不在身后留一条一样的：`hash` 原样带着，别把锚点也一起抹掉。
    void nav?.navigate({ query: rest as typeof route.query, hash: route.hash }, { replace: true })
  }

  async function enter() {
    // 先领回这次连接，再读列表：领到的那一条要出现在读回来的列表里。
    await consumeCallback()
    void load()
  }

  // 首次取数期间占住设置页的显示闸；这一栏没被打开过就不占。
  holdRevealUntil(() => settled.value, enabled, options.revealGate)
  onMounted(() => {
    if (enabled()) enter()
  })
  watch(enabled, (on) => {
    if (on) enter()
    else {
      generation++
      actionGeneration++
      settled.value = false
      for (const name of Object.keys(values)) delete values[name]
      list.value = null
      error.value = ''
      busy.value = ''
    }
  })
  watch(projectId, () => {
    if (!enabled()) return
    actionGeneration++
    for (const name of Object.keys(values)) delete values[name]
    list.value = null
    error.value = ''
    busy.value = ''
    void load()
  })

  return {
    list,
    error,
    busy,
    values,
    form,
    editing,
    load,
    connect,
    disconnect,
    save,
    clear,
    setValue,
    consumeCallback,
    openAdd,
    openEdit,
    submit,
    remove,
  }
}
