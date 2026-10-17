// 打 # 时能引用的文档（#3827）：项目自己的那些，一次取一页，最近改过的在前。
// 和资料库文件的 `libraryApi` 一样是输入框够得着的那一层：输入框要按项目取一次。
import type { LinkableDocument } from './docMentionMenu'

import { listProjectDocuments } from '../api/projectDocuments'

/** 一次最多列这么多份；更多的靠打字筛，菜单里本来也只摆几份。 */
const LINKABLE_PICKS = 100

export async function linkableDocuments(projectId: string): Promise<LinkableDocument[]> {
  return (await listProjectDocuments(projectId, null, LINKABLE_PICKS)).data
}
