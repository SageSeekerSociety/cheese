// 房间里有没有这么一份文件：芝士交付的、人传上来的。
//
// 拆自 `components/WorkPanel.vue`：点一条 `<&路径>` 时，先问房间自己有没有这一份——
// 有就开在房间文件那一格（内容域按路径服务它），没有才去这件任务的树上找。问法与
// `composables/panelPreview/namedFile.ts` 读一份房间文件是同一个调用，只是这里只关心
// 它读不读得到。取数在这一层，组件不直接碰 API 层（`.claude/rules/architecture.md`
// 的组件边界）。
import type { MaybeRefOrGetter } from 'vue'

import { toValue } from 'vue'

import { readPreviewFile } from '@/api'

/** 这一份在房间里读不读得到。`topicId` 还没有（比如刚进这一页）就是没有。 */
export function useRoomFileProbe(topicId: MaybeRefOrGetter<string | null | undefined>) {
  async function exists(path: string): Promise<boolean> {
    const tid = toValue(topicId)
    if (!tid) return false
    try {
      await readPreviewFile(tid, path)
      return true
    } catch {
      return false
    }
  }

  return { exists }
}
