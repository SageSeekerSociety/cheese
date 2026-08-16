import type { Block } from '../cx_types'

export interface PlatformErrorPresentation {
  code: string
  title: string
  body: string
  status: string
  icon: string
  retryable: boolean
}

/**
 * Per-code treatment. A code that is NOT here still renders (see the fallbacks
 * below) — the map exists so a failure whose remedy is specific doesn't get
 * described with the generic one. "平台正在恢复" is a promise, and for a turn
 * that timed out or a message that never reached 芝士 nothing is recovering:
 * the remedy is a human @-ing her again, so the card has to say that instead.
 */
const TREATMENTS: Record<string, { status: string; icon: string }> = {
  storage_exhausted: { status: '自动清理中 · 稍后 @芝士重试', icon: 'mdi-harddisk-alert' },
  runtime_image_missing: {
    status: '平台组件恢复中 · 稍后 @芝士重试',
    icon: 'mdi-package-variant-closed-remove',
  },
  prompt_undelivered: { status: '没送达 · 再 @芝士一次就重开会话', icon: 'mdi-message-alert' },
  turn_timeout: { status: '已强制结束 · 再 @芝士一次接着做', icon: 'mdi-timer-alert' },
}

export function platformErrorPresentation(block: Block): PlatformErrorPresentation | null {
  const meta = block.meta
  if (!meta || meta.event_type !== 'platform_error') return null

  const code = typeof meta.code === 'string' && meta.code ? meta.code : 'platform_error'
  const title = typeof meta.title === 'string' && meta.title ? meta.title : '运行环境暂时不可用'
  const retryable = meta.retryable === true
  const treatment = TREATMENTS[code]

  return {
    code,
    title,
    body: block.content,
    status: treatment?.status ?? (retryable ? '平台正在恢复 · 稍后可重试' : '需要管理员处理'),
    icon: treatment?.icon ?? 'mdi-server-alert',
    retryable,
  }
}
