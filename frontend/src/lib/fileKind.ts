// 一个文件「是哪种类型」只能有一个答案。预览面板用它挑阅读器，输入栏的附件块用
// 它挑图标：两份表各自演进的结果是同一个 .docx 在两处显示成不同的东西。

export interface FileKind {
  label: string
  icon: string
  /** 预览面板把它交给哪个阅读器。 */
  view: 'pages' | 'sheet' | 'markdown'
}

/** 认得出的文档类型。读者能指着什么决定了 view，见 PanelPreview 的说明。 */
export const DOCUMENT_TYPES: Record<string, FileKind> = {
  pdf: { label: 'PDF', icon: 'mdi-file-pdf-box', view: 'pages' },
  docx: { label: 'Word 文档', icon: 'mdi-file-word-outline', view: 'pages' },
  doc: { label: 'Word 文档', icon: 'mdi-file-word-outline', view: 'pages' },
  odt: { label: '文档', icon: 'mdi-file-document-outline', view: 'pages' },
  rtf: { label: '文档', icon: 'mdi-file-document-outline', view: 'pages' },
  pptx: { label: '幻灯片', icon: 'mdi-file-powerpoint-outline', view: 'pages' },
  ppt: { label: '幻灯片', icon: 'mdi-file-powerpoint-outline', view: 'pages' },
  odp: { label: '幻灯片', icon: 'mdi-file-powerpoint-outline', view: 'pages' },
  xlsx: { label: '表格', icon: 'mdi-file-excel-outline', view: 'sheet' },
  xls: { label: '表格', icon: 'mdi-file-excel-outline', view: 'sheet' },
  csv: { label: 'CSV 表格', icon: 'mdi-file-delimited-outline', view: 'sheet' },
  md: { label: 'Markdown', icon: 'mdi-language-markdown-outline', view: 'markdown' },
  markdown: { label: 'Markdown', icon: 'mdi-language-markdown-outline', view: 'markdown' },
}

/** 浏览器自己画不出来、要平台先转成 PDF 的那些。转换走的是 LibreOffice，一份约
 *  2.5 秒，后端按内容哈希缓存。
 *  表格不在里面，而且是故意的：把一张表分页会拆散列、让单元格失去地址，而那正是
 *  它之所以是表的东西。 */
export const NEEDS_CONVERSION = new Set(['docx', 'doc', 'odt', 'rtf', 'pptx', 'ppt', 'odp'])

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
