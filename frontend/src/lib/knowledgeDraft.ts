/**
 * 上传对话框里正在填的那一份东西，以及它会变成什么。
 *
 * 拆之前这份状态是 `Knowledge.vue` 里的一个 `uploadData` ref，连同「提交时往
 * `content` 里塞什么」的一段 if/else 一起长在页里。两件事其实都不是页的事：
 * **填**是对话框自己的（它才知道每一格绑定在哪儿），**变成什么**是一次纯换算
 * （草稿进、`KnowledgeContentData` 出，一次网络都不打）。
 *
 * 于是：类型放这儿给对话框当 v-model 的形状，换算放这儿给 composable 调。这个
 * 文件不 import api、store 或路由。
 */
import type { JSONContent } from '@tiptap/core'
import type { KnowledgeContentData, KnowledgeType } from '@/types'
import type { MaterialType } from '@/types/materials'

/** 对话框里那一份草稿。四个类型各用其中几格，其余留着不动。 */
export interface KnowledgeDraft {
  name: string
  description: string
  type: KnowledgeType
  labels: string[]
  file: File | null
  richTextContent: JSONContent
  url: string
  title: string
  code: string
  language: string
}

/** 每次打开对话框都从这一份开始 —— 上一条填到一半的东西不带过来。 */
export function emptyKnowledgeDraft(): KnowledgeDraft {
  return {
    name: '',
    description: '',
    type: 'MATERIAL',
    labels: [],
    file: null,
    richTextContent: {},
    url: '',
    title: '',
    code: '',
    language: 'javascript',
  }
}

/**
 * 按 MIME 猜这个文件算哪一路材料。
 *
 * 猜错也不要紧：真正说话的是材料记录自己那个 type，这里只决定上传时挂在哪个
 * 名下。认不出来的一律当 `file`（文档）。
 */
export function materialKindForFile(file: File): MaterialType {
  if (file.type.startsWith('image/')) return 'image'
  if (file.type.startsWith('video/')) return 'video'
  if (file.type.startsWith('audio/')) return 'audio'
  return 'file'
}

/** 材料那一档的富文本内容：一段只写着文件名的段落。 */
function materialDocument(fileName: string): JSONContent {
  return {
    type: 'doc',
    content: [
      {
        type: 'paragraph',
        content: [{ type: 'text', text: fileName }],
      },
    ],
  }
}

/**
 * 草稿 → `content` 那一格 JSON。四档各自的形状：
 *
 *   - 材料：富文本，内容是文件名（真正的文件在 `materialId` 上）；
 *   - 文本：编辑器交出来的那份富文本，原样；
 *   - 链接：url / 标题 / 描述；标题留空时退回资料名称；
 *   - 代码片段：代码和语言。
 */
export function knowledgeDraftContent(draft: KnowledgeDraft): KnowledgeContentData {
  switch (draft.type) {
    case 'MATERIAL':
      return draft.file ? { richText: materialDocument(draft.file.name) } : {}
    case 'TEXT':
      return { richText: draft.richTextContent }
    case 'LINK':
      return { url: draft.url, title: draft.title || draft.name, description: draft.description }
    case 'CODE':
      return { code: draft.code, language: draft.language }
  }
}
