/** 芝士输入框里 @ 能引用的一样东西（`GET /assistant/citable`）。 */
export type CitableKind = 'channel' | 'task' | 'doc' | 'file' | 'problem'

export interface Citable {
  /** 写进正文的引用 `<#id>` 里的那个 id：频道、任务、文档是它自己的编号，文件是
   *  `file-<编号>`，题目是 `problem-<编号>`。 */
  id: string
  kind: CitableKind
  title: string
  /** 在哪：项目名、团队名，或「我的资料库」。 */
  where: string
  /** 任务所在的频道。 */
  place?: string
}
