import type { LayoutPanelWidth } from '../layout-area'

/**
 * How wide a task's file tree is: beside the open files in the 文件 panel, and as a column
 * of its own in the task page's 编辑器 form. One remembered width, so the two agree.
 */
export const TASK_FILE_TREE_WIDTH: LayoutPanelWidth = {
  initial: 232,
  min: 160,
  max: 420,
  storageKey: 'cheesex.taskFilesTreeWidth',
}
