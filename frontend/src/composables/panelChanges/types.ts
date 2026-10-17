// 「改动」这一格对外的那一份 props。取数那一半（`../usePanelChanges.ts`）和负责
// 拆开的几个子模块都读它，所以它单独放一处，而不是挂在组合根上。
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
}
