// 我对一个频道的通知档位：三档（所有新消息、@我和我参与的讨论串、静音），静音还能选
// 多久。频道的「通知」那一项点开的下一层、频道详情「设置」里改通知，列的都是这一份。
// 数字和通知怎么跟着档位走是后端的事（`TopicRepository.unread_counts`），这里只把选择
// 交出去。
import type { TopicNotifyLevel } from '@/types/channels'
import type { Command } from '.'

import { t } from '@/i18n'

export type SetNotify = (level: TopicNotifyLevel, until: string | null) => void

function later(ms: number): string {
  return new Date(Date.now() + ms).toISOString()
}

/** 明天早上 9 点（本地时间）。 */
function tomorrowMorning(): string {
  const at = new Date()
  at.setDate(at.getDate() + 1)
  at.setHours(9, 0, 0, 0)
  return at.toISOString()
}

/** 静音多久：到一个时刻，或 null（直到我取消）。时刻在点下去那一刻才算。 */
const DURATIONS: { key: string; label: string; until: () => string | null }[] = [
  { key: 'hour', label: 'work.channel.notify.forHour', until: () => later(60 * 60 * 1000) },
  { key: 'eight', label: 'work.channel.notify.forEightHours', until: () => later(8 * 60 * 60 * 1000) },
  { key: 'tomorrow', label: 'work.channel.notify.untilTomorrow', until: tomorrowMorning },
  { key: 'week', label: 'work.channel.notify.forWeek', until: () => later(7 * 24 * 60 * 60 * 1000) },
  { key: 'forever', label: 'work.channel.notify.untilUnmuted', until: () => null },
]

/** 此刻这一档叫什么：「通知」后面写的值，静音时的记号上悬停看到的也是它。 */
export function notifyLevelName(level: TopicNotifyLevel, mutedUntil: string | null): string {
  if (level === 'all') return t('work.channel.notify.all')
  if (level === 'mentions') return t('work.channel.notify.mentions')
  if (!mutedUntil) return t('work.channel.notify.mute')
  const at = new Date(mutedUntil)
  return t('work.channel.notify.mutedUntil', {
    time: at.toLocaleString(undefined, { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }),
  })
}

/** 通知那一层里的几项：三档，然后是静音多久。此刻的那一档打勾。 */
export function notifyChoices(level: TopicNotifyLevel, mutedUntil: string | null, set: SetNotify): Command[] {
  const muted = level === 'mute'
  return [
    {
      id: 'notify.all',
      title: t('work.channel.notify.all'),
      hint: t('work.channel.notify.allHint'),
      checked: level === 'all',
      run: () => set('all', null),
    },
    {
      id: 'notify.mentions',
      title: t('work.channel.notify.mentions'),
      hint: t('work.channel.notify.mentionsHint'),
      checked: level === 'mentions',
      run: () => set('mentions', null),
    },
    {
      id: 'notify.mute',
      title: t('work.channel.notify.mute'),
      // 静音到某个时刻时，说的是到什么时候；不然是这一档的意思。
      hint: muted && mutedUntil ? notifyLevelName(level, mutedUntil) : t('work.channel.notify.muteHint'),
      checked: muted,
      run: () => set('mute', null),
    },
    ...DURATIONS.map((option, i) => ({
      id: `notify.mute.${option.key}`,
      title: t(option.label),
      divider: i === 0,
      section: i === 0 ? t('work.channel.notify.muteFor') : undefined,
      // 到某个时刻的那几项说不准当初选的是哪一项，只有「直到我取消」认得出来。
      checked: option.key === 'forever' && muted && !mutedUntil,
      run: () => set('mute', option.until()),
    })),
  ]
}
