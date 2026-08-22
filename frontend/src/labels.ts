// Human-facing labels for backend enum values — never show raw enums in the UI.

export const NOTIF_KIND: Record<string, string> = {
  decision_request: '决策请求',
  change_alert: '变更提醒',
  accept_request: '验收卡',
  heartbeat: '巡检',
}

export const TOPIC_STATUS: Record<string, string> = {
  active: '进行中',
  archived: '已归档',
  draft: '草稿',
}

export const TOPIC_KIND: Record<string, string> = {
  root: '全局',
  topic: '话题',
  // 一件事：带分支和验收卡，完成即结束，所在话题照常活着。
  // 历史值：一件活曾经也是一行 topics（迁移 a9f3c7e21b04 之后没有行再带它们）。
  task: '任务',
  subtopic: '分身',
}

export const AI_MODE: Record<string, string> = {
  collaborative: '协作',
  autonomous: '自主',
}

export const PROJECT_ROLE: Record<string, string> = {
  lead: '组长',
  member: '成员',
  mentor: '导师',
}

export function label(map: Record<string, string>, key: string | null | undefined): string {
  if (!key) return ''
  return map[key] ?? key
}
