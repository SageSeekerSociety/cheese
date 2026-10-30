// 一个房间文件开进实时协同：给它一个协作服务认的名字，和一张开这条连接的票。
//
// 和 office 那条（`roomFiles.ts` 的 `openRoomFileEditor`）是同一处的两条路：Word、
// 表格、幻灯片走 OnlyOffice（整份存回），文本走协作服务（几个人同编一份，服务停手
// 几秒后存成这份文件自己的新一版）。分开一个模块，是因为取数那一层按后端域分文件，
// 而这两条路的形状本来就不一样（一条给签过名的配置，一条给名字和票）。
import { request } from './http'

/** 打开一份文本房间文件的协同会话要说的话。`enabled` 为假时 `reason` 是为什么打不开。 */
export interface RoomFileLiveSession {
  enabled: boolean
  /** `not_configured`（这个部署没接协作服务）、`unsupported`（二进制）、
   *  `too_large`（正文超过预览的上限，从不读进内存）。 */
  reason?: 'not_configured' | 'unsupported' | 'too_large'
  /** 协作服务里这一份的名字（`roomfile:<id>`），直接当连接的名字用。 */
  document?: string
  /** 开这条连接的票；重连要重新要一张（票只活两分钟）。 */
  ticket?: string
  /** 只读：服务端照它拒写，屏幕照它收起键盘。 */
  read_only?: boolean
  /** 读这份文件时那一版的哈希。 */
  version?: string | null
  /** 文件现在的正文，给编辑器建初始文档。 */
  content?: string | null
}

export function openRoomFileLive(topicId: string, path: string): Promise<RoomFileLiveSession> {
  return request(`/topics/${encodeURIComponent(topicId)}/files/collab?path=${encodeURIComponent(path)}`)
}
