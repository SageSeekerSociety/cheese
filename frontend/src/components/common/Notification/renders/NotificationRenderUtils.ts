import type { Notification } from '@/types/apiNotifications'
import type { EntityInfo } from '@/types/apiNotifications'

import { topicTitle } from '@/lib/topicState'

/**
 * 通知渲染组件的Props接口
 */
export interface NotificationRenderProps {
  notification: Notification
}

/**
 * 通知渲染结果的接口
 */
export interface RenderedNotificationContent {
  title: string
  body: string
  routerLink?: { name: string; params?: Record<string, any>; query?: Record<string, any> }
  actions?: Array<{
    text: string
    color?: string
    handler: () => void
  }>
}

/**
 * 从通知中提取实体信息
 * @param notification 通知对象
 * @param role 实体角色
 * @returns 实体信息，如果不存在则返回null
 */
export function getEntity(notification: Notification, role: string): EntityInfo | null {
  return notification.entities[role] || null
}

/** A team entity's handle, read off the link the backend resolved for it (`/teams/<handle>`). */
export function teamHandle(team: EntityInfo): string {
  return decodeURIComponent((team.url ?? '').replace(/^\/teams\//, '').split('/')[0])
}

/**
 * 从通知中提取元数据
 * @param notification 通知对象
 * @param key 元数据键
 * @param defaultValue 默认值
 * @returns 元数据值，如果不存在则返回默认值
 */
export function getMetadata<T>(notification: Notification, key: string, defaultValue: T): T {
  const value = notification.contextMetadata[key]
  return value !== undefined ? (value as T) : defaultValue
}

/**
 * 从通知中提取字符串元数据
 * @param notification 通知对象
 * @param key 元数据键
 * @param defaultValue 默认值
 * @returns 元数据值，如果不存在则返回默认值
 */
export function getStringMetadata(notification: Notification, key: string, defaultValue: string = ''): string {
  const value = notification.contextMetadata[key]
  return value !== undefined ? String(value) : defaultValue
}

/** 通知说的那个频道或任务叫什么。 */
export function getRoomTitle(notification: Notification): string {
  return topicTitle({ title: getStringMetadata(notification, 'topicTitle') })
}

/**
 * 点开一条关于房间里某条消息的通知落到哪儿：**那条消息所在的会话** —— 一个任务、一
 * 条支线，或频道自己那条线，并带上 `?block=` 停在那条消息上。指向频道而消息在任务或
 * 支线里，人点开是一页没有这条消息的地方（「这条消息已不存在」）。后端
 * `push.push_link` 是同一条规则。
 */
export function messageLink(notification: Notification): RenderedNotificationContent['routerLink'] {
  const projectId = getStringMetadata(notification, 'projectId')
  const topicId = getStringMetadata(notification, 'topicId')
  if (!projectId || !topicId) return undefined
  const taskId = getStringMetadata(notification, 'taskId')
  const threadId = getStringMetadata(notification, 'threadId')
  const blockId = getStringMetadata(notification, 'blockId')
  const where = taskId
    ? { name: 'workspace-task', params: { projectId, topicId, taskId } }
    : threadId
      ? { name: 'workspace-thread', params: { projectId, topicId, threadId } }
      : { name: 'workspace-topic', params: { projectId, topicId } }
  return { ...where, query: blockId ? { block: blockId } : undefined }
}
