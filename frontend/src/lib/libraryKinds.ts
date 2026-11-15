// 资料库的类型筛选：按后缀把一份文件归进文档、表格、PDF、图片或其他。
import { t } from '@/i18n'

export type Kind = 'all' | 'doc' | 'sheet' | 'pdf' | 'image' | 'code' | 'other'
const KIND_SUFFIXES: Record<Exclude<Kind, 'all' | 'other'>, string[]> = {
  doc: ['doc', 'docx', 'odt', 'rtf', 'md', 'markdown', 'txt', 'ppt', 'pptx', 'odp', 'pages', 'key'],
  sheet: ['xls', 'xlsx', 'csv', 'ods', 'numbers'],
  pdf: ['pdf'],
  image: ['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'heic'],
  code: [
    'py',
    'ipynb',
    'r',
    'm',
    'js',
    'ts',
    'jsx',
    'tsx',
    'vue',
    'html',
    'css',
    'c',
    'cpp',
    'h',
    'java',
    'go',
    'rs',
    'sh',
    'sql',
    'json',
    'yaml',
    'yml',
    'toml',
    'typ',
    'tex',
    'bib',
  ],
}
export const KINDS: Kind[] = ['all', 'doc', 'sheet', 'pdf', 'image', 'code', 'other']
export const KIND_ICONS: Record<Exclude<Kind, 'all'>, string> = {
  doc: 'mdi-file-document-outline',
  sheet: 'mdi-file-table-outline',
  pdf: 'mdi-file-pdf-box',
  image: 'mdi-file-image-outline',
  code: 'mdi-file-code-outline',
  other: 'mdi-file-outline',
}

export function kindOf(path: string): Exclude<Kind, 'all'> {
  const suffix = path.includes('.') ? path.split('.').pop()!.toLowerCase() : ''
  for (const [kind, suffixes] of Object.entries(KIND_SUFFIXES)) {
    if (suffixes.includes(suffix)) return kind as Exclude<Kind, 'all'>
  }
  return 'other'
}

export function kindLabel(kind: Kind): string {
  return {
    all: t('work.library.kinds.all'),
    doc: t('work.library.kinds.doc'),
    sheet: t('work.library.kinds.sheet'),
    pdf: t('work.library.kinds.pdf'),
    image: t('work.library.kinds.image'),
    code: t('work.library.kinds.code'),
    other: t('work.library.kinds.other'),
  }[kind]
}
