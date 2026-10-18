// 「文档」那一格的全部取数：打开文档、读评论串、把名册读成名字，外加评论 @ 了打不开它的
// 人时问一句。结果原样交给只吃 props 的 `components/panels/PanelDoc.vue`。
//
// 调用点：接线外壳 `components/work/PanelDocHost.vue`（对话旁边、资料库、项目目标），
// 和团队文档整页 `views/teams/TeamDocument.vue`——那一页自己取数，画面交给同目录的视图。
import type { ProjectMemberRow } from '../cx_types'
import type { PanelDocProps } from './usePanelDoc'

import { useDocPeople } from './useDocPeople'
import { useDocThreads } from './useDocThreads'
import { useMentionOutside } from './useMentionOutside'
import { usePanelDoc } from './usePanelDoc'

export function usePanelDocWiring(props: PanelDocProps & { members?: ProjectMemberRow[] }) {
  const docPanel = usePanelDoc(props)
  const docThreads = useDocThreads(() => docPanel.documentId.value)
  const docPeople = useDocPeople({
    members: () => props.members ?? [],
    agentHandle: () => props.agentHandle ?? null,
    agentName: () => props.agentName ?? '',
  })
  // 评论 @ 了打不开这份文档的人：问写的人要不要把他们加进频道（和对话里一样）。
  const commentOutside = useMentionOutside({
    place: () => docPanel.documentId.value,
    nameOf: (handle) => docPeople.names.value[handle] ?? handle,
  })
  return { docPanel, docThreads, docPeople, commentOutside }
}

/** 交给面板的那几包；问 @ 了谁的那一包可以不给（预览站里没有它）。 */
export type PanelDocWiring = Omit<ReturnType<typeof usePanelDocWiring>, 'commentOutside'> & {
  commentOutside?: ReturnType<typeof useMentionOutside>
}
