// 一份文档的作者：建成那一刻定下的人和 AI 队友，并列写出来（`DocAuthors.vue`）。
// 人自己建的只有他一个；让 AI 队友写的，是让它写的那个人和那位队友。之后谁再改都
// 不变。平台建的（任务的文档、项目目标）两个都没有，就不写作者。
import type { ProjectMemberRow } from '../cx_types'

import { getAvatarUrl } from '../utils/materials'

import { memberName } from './agentNames'
import { isAgentHandle } from './authorship'

export interface DocAuthor {
  handle: string
  name: string
  /** 这个人自己挑过的头像地址；没挑过、或是 AI 队友时是 null。 */
  avatar: string | null
  agent: boolean
}

export interface AuthoredDocument {
  /** 文档归谁：建它的人，或让 AI 队友写它的人。 */
  created_by?: string | null
  /** 写它的 AI 队友。 */
  drafted_by?: string | null
}

/** 人在前，AI 队友在后。名字和头像从名册里认；名册上没有的人写账号，没有的队友
 *  写项目给 AI 队友起的名字。 */
export function docAuthors(doc: AuthoredDocument, members: ProjectMemberRow[], agentName: string): DocAuthor[] {
  return [doc.created_by, doc.drafted_by]
    .filter((h): h is string => !!h)
    .map((handle) => {
      const row = members.find((m) => m.user_handle === handle)
      if (isAgentHandle(handle)) return { handle, name: memberName(row) || agentName, avatar: null, agent: true }
      return {
        handle,
        name: memberName(row) || handle,
        avatar: row?.avatar_id != null ? getAvatarUrl(row.avatar_id) : null,
        agent: false,
      }
    })
}
