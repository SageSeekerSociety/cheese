// Human-facing labels for backend enum values — never show raw enums in the UI.
// Each map points an enum value at its catalog key; `label` renders it in the
// reader's language.
import { t } from '@/i18n'

export const NOTIF_KIND: Record<string, string> = {
  decision_request: 'work.labels.notifKind.decisionRequest',
  change_alert: 'work.labels.notifKind.changeAlert',
  accept_request: 'work.labels.notifKind.acceptRequest',
}

export const TOPIC_STATUS: Record<string, string> = {
  active: 'work.labels.topicStatus.active',
  archived: 'work.labels.topicStatus.archived',
  draft: 'work.labels.topicStatus.draft',
}

export function label(map: Record<string, string>, key: string | null | undefined): string {
  if (!key) return ''
  const id = map[key]
  return id ? t(id) : key
}
