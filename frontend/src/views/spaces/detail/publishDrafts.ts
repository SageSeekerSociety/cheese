// 从文件里读出的题目：一道一道在发题页上改，勾上的一起发出去。
import type { JSONContent } from '@tiptap/core'
import type { PdfTaskDraftData } from '@/network/api/tasks/types'

export interface PublishDraft {
  key: string
  picked: boolean
  name: string
  /** 后端读出的简介；发出去时前面加上出处。 */
  intro: string
  description: JSONContent
  /** 出处页。 */
  page: number
}

/**
 * 草稿的出处页。接口不回传页码，但后端是一页一页读、按页序把草稿攒起来的，所以第 N
 * 条就是第 N 页；某页解析失败被跳过时这个号会偏小，这是唯一会失准的地方。
 */
function draftPage(index: number): number {
  return index + 1
}

/**
 * 出处标记。题目模型里没有「来源」这一列，标记写进**简介**：简介跟着这道题一路走，
 * 审核队列和题目页把它认出来单独显示（`views/spaces/model.ts` 的 `splitOrigin`）。
 */
function originTag(page: number): string {
  return `【PDF · 第 ${page} 页】` // i18n-data: 写进题目简介的固定标记，model.ts 的 ORIGIN_PREFIX 按它剥离
}

export function toDrafts(
  drafts: PdfTaskDraftData[],
  readDescription: (markdown: string) => JSONContent
): PublishDraft[] {
  return drafts.map((draft, index) => ({
    key: `${index}-${draft.name || 'draft'}`,
    picked: true,
    name: draft.name ?? '',
    intro: draft.intro ?? '',
    description: readDescription(draft.description ?? ''),
    page: draftPage(index),
  }))
}

/** 文档里有没有东西：一个字、一张图、一段视频都算。 */
export function hasContent(doc: JSONContent | undefined): boolean {
  if (!doc) return false
  if (doc.type === 'text') return Boolean(doc.text?.trim())
  if (doc.type && !['doc', 'paragraph', 'hardBreak'].includes(doc.type) && !doc.content) return true
  return (doc.content ?? []).some(hasContent)
}

/** 这一道还缺什么：没名称、没描述。都不缺就是空数组。 */
export function draftGaps(draft: PublishDraft): ('name' | 'description')[] {
  const gaps: ('name' | 'description')[] = []
  if (!draft.name.trim()) gaps.push('name')
  if (!hasContent(draft.description)) gaps.push('description')
  return gaps
}

export function toDraftPayload(draft: PublishDraft, spaceId: number): PdfTaskDraftData {
  return {
    name: draft.name.trim(),
    intro: `${originTag(draft.page)}${draft.intro.trim()}`.slice(0, 255),
    description: JSON.stringify(draft.description),
    space: spaceId,
  }
}
