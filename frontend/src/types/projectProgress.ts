/** 项目总览「最近进展」里的一条（后端 `project_progress/feed.py`）。 */
export type ProgressKind =
  | 'created'
  | 'started'
  | 'accepted'
  | 'completed'
  | 'closed'
  | 'returned'
  | 'stalled'
  | 'version'

export interface ProgressItem {
  kind: ProgressKind
  at: string
  /** 是哪件任务；产物的新版本没有任务。 */
  taskId: string | null
  taskTitle: string
  taskTitleSource: string | null
  roomId: string | null
  /** 谁做的；停滞、新版本没有人。 */
  by: string | null
  /** `by` 是 GitHub 登录名而不是平台 handle：不去名册里找同名的账号。 */
  byGithub?: boolean
  /** 产物的新版本：哪一项、第几版。 */
  artifactId: string | null
  artifactName: string
  version: number | null
}
