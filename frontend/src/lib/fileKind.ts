// 一个文件「是哪种类型」只能有一个答案。预览面板用它挑阅读器，输入栏的附件块用
// 它挑图标：两份表各自演进的结果是同一个 .docx 在两处显示成不同的东西。

import { t } from '@/i18n'

export interface FileKind {
  /** 取词函数，不是字符串：这张表在模块加载时就建好了，写死的字符串会停在本次
   *  locale 上（模块级常量里 `t()` 只算一次）。取词留到渲染时，切语言当场跟着换。 */
  label: () => string
  icon: string
  /** 预览面板把它交给哪个阅读器。 */
  view: 'pages' | 'sheet' | 'markdown'
}

/** 认得出的文档类型。读者能指着什么决定了 view，见 PanelPreview 的说明。 */
export const DOCUMENT_TYPES: Record<string, FileKind> = {
  pdf: { label: () => t('workspace.preview.typePdf'), icon: 'mdi-file-pdf-box', view: 'pages' },
  docx: { label: () => t('workspace.preview.typeWord'), icon: 'mdi-file-word-outline', view: 'pages' },
  doc: { label: () => t('workspace.preview.typeWord'), icon: 'mdi-file-word-outline', view: 'pages' },
  odt: { label: () => t('workspace.preview.typeDocument'), icon: 'mdi-file-document-outline', view: 'pages' },
  rtf: { label: () => t('workspace.preview.typeDocument'), icon: 'mdi-file-document-outline', view: 'pages' },
  pptx: { label: () => t('workspace.preview.typeSlides'), icon: 'mdi-file-powerpoint-outline', view: 'pages' },
  ppt: { label: () => t('workspace.preview.typeSlides'), icon: 'mdi-file-powerpoint-outline', view: 'pages' },
  odp: { label: () => t('workspace.preview.typeSlides'), icon: 'mdi-file-powerpoint-outline', view: 'pages' },
  xlsx: { label: () => t('workspace.preview.typeSheet'), icon: 'mdi-file-excel-outline', view: 'sheet' },
  xls: { label: () => t('workspace.preview.typeSheet'), icon: 'mdi-file-excel-outline', view: 'sheet' },
  csv: { label: () => t('workspace.preview.typeCsv'), icon: 'mdi-file-delimited-outline', view: 'sheet' },
  md: { label: () => t('workspace.preview.typeMarkdown'), icon: 'mdi-language-markdown-outline', view: 'markdown' },
  markdown: {
    label: () => t('workspace.preview.typeMarkdown'),
    icon: 'mdi-language-markdown-outline',
    view: 'markdown',
  },
}

/** 浏览器自己画不出来、要平台先转成 PDF 的那些。转换走的是 LibreOffice，一份约
 *  2.5 秒，后端按内容哈希缓存。
 *  表格不在里面，而且是故意的：把一张表分页会拆散列、让单元格失去地址，而那正是
 *  它之所以是表的东西。 */
export const NEEDS_CONVERSION = new Set(['docx', 'doc', 'odt', 'rtf', 'pptx', 'ppt', 'odp'])

/** 浏览器自己画得出来的图片。它们不是文档，所以不在上面那张表里，但预览域按
 *  image/png、image/jpeg 把字节发出来，浏览器画得出来。 */
export const IMAGE_SUFFIXES = new Set(['png', 'jpg', 'jpeg'])

/** 预览面板自己显示得出这个文件吗。
 *
 *  一枚 `<&路径>` chip 只是一个路径，不带它在哪个库；要决定把它开在哪一格，先得
 *  知道预览显示不显示得了它。 */
export function previewCanShow(path: string): boolean {
  const suffix = suffixOf(path)
  return !!DOCUMENT_TYPES[suffix] || IMAGE_SUFFIXES.has(suffix)
}

/** 文本 diff 读不了的文档。
 *
 *  它的字节是压缩包或者二进制，git 只会说「二进制文件不同」，所以改动那一格对它得
 *  换一副面孔：画出这一版的页面，外加文件自己带的修订。`.md` 和 `.csv` 不在里面——
 *  它们的文本 diff 正是审阅最需要的那一面，换成渲染反而更差。 */
export function needsDocumentView(path: string): boolean {
  const suffix = suffixOf(path)
  return NEEDS_CONVERSION.has(suffix) || suffix === 'pdf' || suffix === 'xlsx' || suffix === 'xls'
}

/** 这个文件有没有「第一页」可以画出来。PDF 直接就有，Office 文档转一次就有。 */
export function hasPagePreview(path: string): boolean {
  const suffix = suffixOf(path)
  return suffix === 'pdf' || NEEDS_CONVERSION.has(suffix)
}

export function suffixOf(path: string): string {
  const name = path.split('/').pop() ?? ''
  const dot = name.lastIndexOf('.')
  return dot > 0 ? name.slice(dot + 1).toLowerCase() : ''
}

/** 附件块左边那个方格里的图标，给没有缩略图可画的类型用。认不出的拿一张白纸:
 *  不是出错，只是这个类型没有更具体的说法。 */
export function fileIcon(path: string): string {
  return DOCUMENT_TYPES[suffixOf(path)]?.icon ?? 'mdi-file-outline'
}
