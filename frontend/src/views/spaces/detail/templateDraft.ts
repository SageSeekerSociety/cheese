import type { JSONContent } from '@tiptap/core'

/** 题目模板那一张表单改的草稿：容器把读回来的模板落成它，画面在它上面改。 */
export interface TemplateDraft {
  name: string
  description: string
  title: string
  content: JSONContent
  submitterType: 'USER' | 'TEAM' | null
  rank: number | null
  minTeamSize: number
  maxTeamSize: number
  defaultDeadline: number | null
  requireRealName: boolean | null
}

/** 新建时的空白草稿；容器读不到模板时也用它兜底。 */
export function blankTemplate(): TemplateDraft {
  return {
    name: '',
    description: '',
    title: '',
    content: {},
    submitterType: null,
    rank: null,
    minTeamSize: 1,
    maxTeamSize: 10,
    defaultDeadline: null,
    requireRealName: null,
  }
}
