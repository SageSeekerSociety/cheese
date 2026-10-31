// 项目的 MCP 服务器：来自默认分支的 .mcp.json、项目设置和队友类型；连接属于项目。
// 后端从不返回令牌和密钥的值，这里的类型里也没有它们。
//
// 从 `api.ts` 拆出来：那个文件在上限之上只能变短，而这一条线自成一块——设置页的
// `/projects/{id}/mcp/*` 和房间只读的 `/topics/{id}/mcp/servers`。
import { request } from '../api'

export type McpServerStatus = 'connected' | 'disconnected' | 'needs_reconnect' | 'missing_values' | 'ready'
export interface McpVariable {
  name: string
  set: boolean
  updated_by: string | null
  updated_at: string | null
}
/** 本地命令类服务器在项目机器上的情况。 */
export interface McpRun {
  /** idle 未启动；awaiting 等设备主人允许；refused 设备主人没有允许。 */
  state: 'idle' | 'running' | 'failed' | 'awaiting' | 'refused'
  /** 启动失败时日志的最后几行。 */
  log: string[]
  /** 项目机器是某位成员自己的设备时，那位成员；云端机器是 null。 */
  owner: string | null
}
export interface McpDeclaringType {
  name: string
  title: string
}
export interface McpServer {
  name: string
  transport: 'http' | 'sse' | 'stdio'
  /** 远程的是地址的主机名；本地命令类是空串。 */
  host: string
  /** 本地命令类要运行的那一行命令；远程的是 null。 */
  command: string | null
  /** 本地命令类在项目机器上的情况；远程的是 null。 */
  run: McpRun | null
  auth: 'oauth' | 'headers'
  status: McpServerStatus
  /** 它从哪来：null 是项目的 .mcp.json，否则是声明它的那几个队友类型。 */
  declared_by: McpDeclaringType[] | null
  /** 在项目设置里添加的那一条：谁、什么时候加的；远程的带完整地址和请求头名，本地命令带环境变量。别的来源是 null。 */
  added: {
    by: string
    at: string
    url: string | null
    header: string | null
    env: Record<string, string> | null
  } | null
  /** 在项目设置里加的这一条被仓库 .mcp.json 里的同名服务器盖住了。 */
  shadowed: boolean
  authorized_by: string | null
  authorized_at: string | null
  variables: McpVariable[]
}
/** 「添加 / 编辑 MCP 服务器」那张表交出来的东西：远程服务器，或在项目机器上运行的命令。 */
export type McpServerDraft =
  | {
      kind: 'remote'
      name: string
      url: string
      auth: 'oauth' | 'headers'
      /** 请求头的名字（填写请求头时）。 */
      header: string | null
      /** 请求头的值；编辑时不改就是 null。 */
      value: string | null
    }
  | {
      kind: 'command'
      name: string
      /** 整行命令，按 shell 的写法拆开。 */
      command: string
      env: Record<string, string>
    }
export interface McpServerList {
  servers: McpServer[]
  /** 读不出清单时的原因：没有 .mcp.json、格式不对、仓库暂时读不到。 */
  problem: 'missing' | 'invalid' | 'unreadable' | null
}
export type RoomMcpServer = Pick<
  McpServer,
  'name' | 'host' | 'auth' | 'status' | 'declared_by' | 'authorized_by' | 'authorized_at'
>

export function getMcpServers(projectId: string): Promise<McpServerList> {
  return request(`/projects/${encodeURIComponent(projectId)}/mcp/servers`)
}
export function connectMcpServer(projectId: string, name: string): Promise<{ authorization_url: string }> {
  return request(`/projects/${encodeURIComponent(projectId)}/mcp/servers/${encodeURIComponent(name)}/connect`, {
    method: 'POST',
  })
}
/** 从授权服务器回来时地址栏里的 `mcp_handoff`：回调没存任何东西，只把结果替发起连接的
 *  成员留着。设置页登录着去领，只有发起人领得到；回的是这次连接的结果（`mcp` 加
 *  `mcp_result` 或 `mcp_error`），和回调以前直接落在地址栏里的一样。 */
export function claimMcpHandoff(handoff: string): Promise<{ query: Record<string, string> }> {
  return request(`/mcp/oauth/handoffs/${encodeURIComponent(handoff)}`, { method: 'POST' })
}
export function disconnectMcpServer(projectId: string, name: string): Promise<null> {
  return request(`/projects/${encodeURIComponent(projectId)}/mcp/servers/${encodeURIComponent(name)}/connection`, {
    method: 'DELETE',
  })
}
export function setMcpSecret(projectId: string, name: string, value: string): Promise<null> {
  return request(`/projects/${encodeURIComponent(projectId)}/mcp/secrets/${encodeURIComponent(name)}`, {
    method: 'PUT',
    body: JSON.stringify({ value }),
  })
}
export function clearMcpSecret(projectId: string, name: string): Promise<null> {
  return request(`/projects/${encodeURIComponent(projectId)}/mcp/secrets/${encodeURIComponent(name)}`, {
    method: 'DELETE',
  })
}
export function getRoomMcpServers(topicId: string): Promise<{ servers: RoomMcpServer[] }> {
  return request(`/topics/${encodeURIComponent(topicId)}/mcp/servers`)
}
export function addMcpServer(projectId: string, draft: McpServerDraft): Promise<null> {
  return request(`/projects/${encodeURIComponent(projectId)}/mcp/servers`, {
    method: 'POST',
    body: JSON.stringify(draft),
  })
}
export function updateMcpServer(projectId: string, draft: McpServerDraft): Promise<null> {
  return request(`/projects/${encodeURIComponent(projectId)}/mcp/servers/${encodeURIComponent(draft.name)}`, {
    method: 'PUT',
    body: JSON.stringify(draft),
  })
}
export function removeMcpServer(projectId: string, name: string): Promise<null> {
  return request(`/projects/${encodeURIComponent(projectId)}/mcp/servers/${encodeURIComponent(name)}`, {
    method: 'DELETE',
  })
}

/** 电脑的主人答一条运行请求：允许这条命令在这台电脑上运行，或不允许。 */
export function decideMcpRun(id: string, allow: boolean): Promise<{ id: string; allowed: boolean }> {
  return request(`/mcp/approvals/${encodeURIComponent(id)}`, {
    method: 'POST',
    body: JSON.stringify({ allow }),
  })
}
