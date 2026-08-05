import type { Block } from '../cx_types'

export interface PlatformErrorPresentation {
  code: string
  title: string
  body: string
  status: string
  icon: string
  retryable: boolean
}

export function platformErrorPresentation(block: Block): PlatformErrorPresentation | null {
  const meta = block.meta
  if (!meta || meta.event_type !== 'platform_error') return null

  const code = typeof meta.code === 'string' && meta.code ? meta.code : 'platform_error'
  const title = typeof meta.title === 'string' && meta.title ? meta.title : '运行环境暂时不可用'
  const retryable = meta.retryable === true

  return {
    code,
    title,
    body: block.content,
    status:
      code === 'storage_exhausted'
        ? '自动清理中 · 稍后 @芝士重试'
        : retryable
          ? '平台正在恢复 · 稍后可重试'
          : '需要管理员处理',
    icon: code === 'storage_exhausted' ? 'mdi-harddisk-alert' : 'mdi-server-alert',
    retryable,
  }
}
