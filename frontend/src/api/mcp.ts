// 项目的远程 MCP 服务器：来自默认分支的 .mcp.json；连接属于项目，任何成员都能连接或断开。
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
export interface McpDeclaringType {
  name: string
  title: string
}
export interface McpServer {
  name: string
  transport: 'http' | 'sse'
  host: string
  auth: 'oauth' | 'headers'
  status: McpServerStatus
  /** 它从哪来：null 是项目的 .mcp.json，否则是声明它的那几个队友类型。 */
  declared_by: McpDeclaringType[] | null
  authorized_by: string | null
  authorized_at: string | null
  variables: McpVariable[]
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
