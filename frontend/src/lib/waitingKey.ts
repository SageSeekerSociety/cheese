import type { WaitingItem } from '@/cx_types'

/** 「等你处理」里一行的身份：处理完按它拿掉，按钮上的「正在交」也按它认。 */
export function waitingKey(item: WaitingItem): string {
  return item.kind === 'work'
    ? `work:${item.topicId}:${item.taskId ?? ''}:${item.blockId ?? ''}:${item.alertId ?? ''}`
    : `${item.kind}:${item.id}`
}
