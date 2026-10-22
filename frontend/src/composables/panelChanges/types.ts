// 「改动」这一格对外的那一份 props。取数那一半（`../usePanelChanges.ts`）和负责
// 拆开的几个子模块都读它，所以它单独放一处，而不是挂在组合根上。
/**
 * 现场工作区没有及时响应时，这一格现在拿什么顶上：
 * - `none`：正常，看的来源就是那一面。
 * - `committed`：已切到已提交版本。差异和文件都从代码托管读，重试现场不挡着看。
 * - `unavailable`：现场没答上来，已提交版本也拿不到；这一格是空的，但不再拿一句
 *   请求超时把整个改动页顶掉。
 */
export type LiveSourceFallback = 'none' | 'committed' | 'unavailable'

export interface PanelChangesProps {
  topicId: string | null
  projectId: string | null
  /** 这一格看的那件任务。 */
  taskId: string | null
  readOnly?: boolean
  /** This tab is the one on screen. Loads happen on the rising edge, exactly
   *  like opening the old drawer did. */
  active?: boolean
  /** Bumped by WorkPanel when a turn ends — the moment 芝士's commits and its
   *  working tree actually changed. Silent re-fetch, never a spinner. */
  refreshTick?: number
  /** 这间房的队友叫什么；任务单独指定了别的队友时按 `memberNames` 认它。状态那一句
   *  （「小 C 在做」）说到它。 */
  agentName?: string
  /** handle → 名册上的名字。 */
  memberNames?: Record<string, string>
}
