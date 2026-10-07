// 项目设置页「远程 MCP 服务器」这一节的数据：清单、连接状态、每一行那几个变量的
// 值，以及从授权服务器回来时带在地址栏里的那一次结果。
//
// 拆自 `components/ProjectMcpSettings.vue`：取数与写都在这一层，组件只画。清单来自
// 仓库默认分支的 .mcp.json，连接属于项目 —— 任何成员都能连接或断开，谁授权的写在
// 每一行上。
//
// 连接、断开、填密钥都是一次性动作：结果跟着这一次点击走，用全局 toast 说一声，
// 所以这里没有 `useSaveState` 那种就地回执。
import type { McpServer, McpServerList } from '@/api'

import { reactive, ref } from 'vue'
import { toast } from 'vuetify-sonner'

import { useNavigation } from '@/composables/useNavigation'

import { clearMcpSecret, connectMcpServer, disconnectMcpServer, getMcpServers, setMcpSecret } from '@/api'
import { t } from '@/i18n'
import { goAuthorize } from '@/lib/desktopApp'
import { renderNoticeMessage } from '@/lib/noticeText'

export function useProjectMcp(projectId: () => string) {
  const nav = useNavigation()
  const list = ref<McpServerList | null>(null)
  const error = ref('')
  // 哪一行的动作在跑：服务器名，或者某个变量名 —— 一行一次只跑一个动作。
  const busy = ref('')
  const values = reactive<Record<string, string>>({})

  async function load() {
    error.value = ''
    try {
      list.value = await getMcpServers(projectId())
    } catch (e) {
      error.value = e instanceof Error ? e.message : t('work.mcp.loadFailed')
    }
  }

  function fail(e: unknown, fallback: string) {
    toast.error(e instanceof Error ? e.message : fallback)
  }

  async function connect(server: McpServer) {
    busy.value = server.name
    try {
      const { authorization_url } = await connectMcpServer(projectId(), server.name)
      // 返 true 是桌面端开了新窗口、页面还在，清掉 busy；返 false 是浏览器整页跳走，
      // busy 留着不管——回来时组件重挂载会自己清掉。
      if (goAuthorize(authorization_url)) busy.value = ''
    } catch (e) {
      fail(e, t('work.mcp.connectFailed'))
      busy.value = ''
    }
  }

  async function disconnect(server: McpServer) {
    busy.value = server.name
    try {
      await disconnectMcpServer(projectId(), server.name)
      await load()
    } catch (e) {
      fail(e, t('work.mcp.disconnectFailed'))
    } finally {
      busy.value = ''
    }
  }

  /** 存一个变量的值。空的不发，输入框里那一份存完就清掉（它只用来输入）。 */
  async function save(name: string) {
    const value = values[name]?.trim()
    if (!value) return
    busy.value = name
    try {
      await setMcpSecret(projectId(), name, value)
      values[name] = ''
      await load()
    } catch (e) {
      fail(e, t('work.mcp.saveFailed'))
    } finally {
      busy.value = ''
    }
  }

  async function clear(name: string) {
    busy.value = name
    try {
      await clearMcpSecret(projectId(), name)
      await load()
    } catch (e) {
      fail(e, t('work.mcp.saveFailed'))
    } finally {
      busy.value = ''
    }
  }

  // 从授权服务器回来：把结果说一次，再从地址栏拿掉，刷新不会再说一遍。
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

  function takeCallbackResult() {
    const route = nav?.route
    if (!route) return
    const { mcp, mcp_result: result, mcp_error: failure, mcp_error_params: failureParams, ...rest } = route.query
    if (!result && !failure) return
    const name = String(mcp ?? '')
    if (failure) toast.error(failureText(name, failure, failureParams))
    else toast.success(t('work.mcp.connectedNotice', { name }))
    // 换掉地址、不在身后留一条一样的：`hash` 原样带着，别把锚点也一起抹掉。
    void nav?.navigate({ query: rest, hash: route.hash }, { replace: true })
  }

  return { list, error, busy, values, load, connect, disconnect, save, clear, takeCallbackResult }
}
