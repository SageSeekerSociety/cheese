// 一个文件「是哪种类型」只能有一个答案。预览面板用它挑阅读器，输入栏的附件块用
// 它挑图标：两份表各自演进的结果是同一个 .docx 在两处显示成不同的东西。

import { t } from '@/i18n'

export interface FileKind {
  label: string
  icon: string
  /** 预览面板把它交给哪个阅读器。 */
  view: 'pages' | 'sheet' | 'markdown'
  /** 表格按哪种读法打开。字节本身看不出区别，所以这是这一行类型自己的事，只对
   *  `view: 'sheet'` 的那些有意义；不是表格就没有这一项。 */
  sheet?: 'workbook' | 'csv' | 'tsv' | 'ods'
}

/** 类型名按当前语言取：`label` 是 getter，每次读都查一次目录，切换语言后跟着变。 */
function named<T extends { icon: string; view?: FileKind['view'] }>(labelKey: string, rest: T): T & { label: string } {
  return Object.defineProperty({ ...rest }, 'label', {
    enumerable: true,
    get: () => t(`work.room.fileKind.${labelKey}`),
  }) as T & { label: string }
}

/** 认得出的文档类型。读者能指着什么决定了 view，见 PanelPreview 的说明。 */
export const DOCUMENT_TYPES: Record<string, FileKind> = {
  pdf: { label: 'PDF', icon: 'mdi-file-pdf-box', view: 'pages' },
  docx: named('word', { icon: 'mdi-file-word-outline', view: 'pages' }),
  doc: named('word', { icon: 'mdi-file-word-outline', view: 'pages' }),
  odt: named('document', { icon: 'mdi-file-document-outline', view: 'pages' }),
  rtf: named('document', { icon: 'mdi-file-document-outline', view: 'pages' }),
  pptx: named('slides', { icon: 'mdi-file-powerpoint-outline', view: 'pages' }),
  ppt: named('slides', { icon: 'mdi-file-powerpoint-outline', view: 'pages' }),
  odp: named('slides', { icon: 'mdi-file-powerpoint-outline', view: 'pages' }),
  xlsx: named('sheet', { icon: 'mdi-file-excel-outline', view: 'sheet', sheet: 'workbook' }),
  xlsm: named('sheet', { icon: 'mdi-file-excel-outline', view: 'sheet', sheet: 'workbook' }),
  xls: named('sheet', { icon: 'mdi-file-excel-outline', view: 'sheet', sheet: 'workbook' }),
  ods: named('sheet', { icon: 'mdi-file-excel-outline', view: 'sheet', sheet: 'ods' }),
  csv: named('csv', { icon: 'mdi-file-delimited-outline', view: 'sheet', sheet: 'csv' }),
  tsv: { label: 'TSV', icon: 'mdi-file-delimited-outline', view: 'sheet', sheet: 'tsv' },
  md: { label: 'Markdown', icon: 'mdi-language-markdown-outline', view: 'markdown' },
  markdown: { label: 'Markdown', icon: 'mdi-language-markdown-outline', view: 'markdown' },
}

/** 浏览器自己画不出来、要平台先转成 PDF 的那些。转换走的是 LibreOffice，一份约
 *  2.5 秒，后端按内容哈希缓存。
 *  表格不在里面，而且是故意的：把一张表分页会拆散列、让单元格失去地址，而那正是
 *  它之所以是表的东西。 */
export const NEEDS_CONVERSION = new Set(['docx', 'doc', 'odt', 'rtf', 'pptx', 'ppt', 'odp'])

/** 还有「网页」这一种读法的那几个。和 `NEEDS_CONVERSION` 不是同一张表，虽然重叠：
 *  转成网页的是 OfficeCLI，它只认这三种，而 `.doc`/`.odt`/`.rtf` 它读不了。表格在
 *  那边是「不转」，在这边恰恰是要转的那一种——一张表失去单元格地址就不成其为表，
 *  而网页恰恰把地址留在元素上。 */
export const PAGE_FORMATS = new Set(['docx', 'xlsx', 'pptx'])

/** 这一份能不能换成网页读。
 *
 *  收路径，因为它总是和「读者按了没有」一起用，而那一格认得的是文件。 */
export function pageViewOf(path: string | null | undefined): boolean {
  return !!path && PAGE_FORMATS.has(suffixOf(path))
}

/** 浏览器自己画得出来的图片。它们不是文档，所以不在上面那张表里，但预览域按
 *  对应的 image/* 把字节发出来，浏览器画得出来。 */
export const IMAGE_SUFFIXES = new Set(['png', 'jpg', 'jpeg', 'gif', 'webp'])

/** 图片字节的 mime。预览域按 artifact 自己的 mime 发字节，改动那一格从原始字节
 *  造 Blob 时要的是同一个答案。 */
const IMAGE_MIME: Record<string, string> = {
  png: 'image/png',
  jpg: 'image/jpeg',
  jpeg: 'image/jpeg',
  gif: 'image/gif',
  webp: 'image/webp',
}

export function imageMimeOf(suffix: string): string {
  return IMAGE_MIME[suffix] ?? 'image/jpeg'
}

/** 预览面板自己显示得出这个文件吗。
 *
 *  一枚 `<&路径>` chip 只是一个路径，不带它在哪个库；要决定把它开在哪一格，先得
 *  知道预览显示不显示得了它。 */
export function previewCanShow(path: string): boolean {
  const suffix = suffixOf(path)
  return !!DOCUMENT_TYPES[suffix] || IMAGE_SUFFIXES.has(suffix)
}

/** 房间里这一份，预览面板画得出来吗。
 *
 *  和 `previewCanShow` 只差网页这一档，而那一档恰恰由「它在哪个库」决定：仓库树里
 *  的 .html 是源码，该开在「改动」那格看 diff；房间文件没有树、只有那几行字节，所
 *  以把它交给预览域的沙箱 iframe。 */
export function previewCanShowInRoom(path: string): boolean {
  return previewCanShow(path) || isWebPage(path)
}

/** 网页类文件：浏览器自己画得出，但要有内容域才画得出来（见 `WEB_TYPES`）。 */
export function isWebPage(path: string): boolean {
  return suffixOf(path) in WEB_TYPES
}

/** 网页类文件按什么 mime 发出去。内容域按路径的扩展名猜，这里说的是同一件事，
 *  只是面板在把地址递过去之前就要把类型写在预览条上。 */
export function webMimeOf(suffix: string): string {
  return WEB_MIME[suffix] ?? 'text/html'
}

/** 文本 diff 读不了的文档。
 *
 *  它的字节是压缩包或者二进制，git 只会说「二进制文件不同」，所以改动那一格对它得
 *  换一副面孔：画出这一版的页面，外加文件自己带的修订。`.md` 和 `.csv` 不在里面——
 *  它们的文本 diff 正是审阅最需要的那一面，换成渲染反而更差。 */
export function needsDocumentView(path: string): boolean {
  const suffix = suffixOf(path)
  return NEEDS_CONVERSION.has(suffix) || ['pdf', 'xlsx', 'xlsm', 'xls', 'ods'].includes(suffix)
}

/** 表格阅读器要按哪种读法打开这一份。收后缀（`suffixOf` 的结果），不是路径。
 *
 *  答案就在上面那张表里（`FileKind.sheet`），这里只是把「认得出的后缀 → 读法」
 *  这一步包成一个函数：调用方手上常常只有一个后缀，没有那一行类型。认不出的后缀
 *  按 OOXML 工作簿读，和原来一样。 */
export function sheetKindOf(suffix: string): 'workbook' | 'csv' | 'tsv' | 'ods' {
  return DOCUMENT_TYPES[suffix]?.sheet ?? 'workbook'
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
  return DOCUMENT_TYPES[suffixOf(path)]?.icon ?? WEB_TYPES[suffixOf(path)]?.icon ?? 'mdi-file-outline'
}

/** 浏览器直接画得出、因此 DOCUMENT_TYPES 里没有的那几种。
 *
 *  它们不进 DOCUMENT_TYPES 是有讲究的：那张表同时决定 `previewCanShow`，而一枚
 *  指着仓库里某个 .html 源文件的 chip 该开在「改动」那一格去看 diff，不是开进
 *  阅读器。这里只管「它叫什么」，不管「谁来显示它」——所以「房间里那一种开在哪里」
 *  是 `previewCanShowInRoom` 说的，它多认这一档。 */
const WEB_TYPES: Record<string, { label: string; icon: string }> = {
  html: named('webPage', { icon: 'mdi-language-html5' }),
  htm: named('webPage', { icon: 'mdi-language-html5' }),
  svg: named('vector', { icon: 'mdi-vector-square' }),
}

const WEB_MIME: Record<string, string> = {
  html: 'text/html',
  htm: 'text/html',
  svg: 'image/svg+xml',
}

/** 说给人听的类型名（「PDF」「网页」「表格」）。认不出就是中性的「文件」——
 *  一个说不出的类型不该在界面上变成一段后缀。 */
export function fileLabel(path: string): string {
  const suffix = suffixOf(path)
  return DOCUMENT_TYPES[suffix]?.label ?? WEB_TYPES[suffix]?.label ?? t('work.room.fileKind.file')
}
