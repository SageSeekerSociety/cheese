// 比较两版时后端给的那几种说明（为什么没有逐行差异、比的是什么），换成屏幕上的话。
import { t } from '@/i18n'

export function noteText(note: string | null | undefined): string {
  switch (note) {
    case 'oversized':
      return t('tasks.artifactComparison.oversized')
    case 'binary':
      return t('tasks.artifactComparison.binary')
    case 'unsupported':
      return t('tasks.artifactComparison.unsupported')
    case 'many':
      return t('tasks.artifactComparison.many')
    case 'unavailable':
      return t('tasks.artifactComparison.unavailable')
    case 'source':
      return t('tasks.artifactComparison.source')
    case 'link':
      return t('tasks.artifactComparison.link')
    default:
      return ''
  }
}

export function statusText(status: string | undefined): string {
  switch (status) {
    case 'added':
      return t('tasks.artifact.status.added')
    case 'removed':
      return t('tasks.artifact.status.removed')
    case 'modified':
      return t('tasks.artifact.status.modified')
    default:
      return ''
  }
}
