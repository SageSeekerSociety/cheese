// 现场顶上那一行的那个词（思考中、正在读文件、重试中……）。现场顶栏和对话里在动的
// 头像说的是同一句话，所以只在这里写一遍。

import type { SiteState, SiteStatus } from './siteStatus'

import { t } from '@/i18n'

// 键名写全，不拼：拼出来的键谁也搜不到，目录那道闸门会把它们当成没人用的。
const PLAIN: Record<Exclude<SiteState, 'acting' | 'retrying'>, string> = {
  thinking: 'work.room.site.status.thinking',
  compacting: 'work.room.site.status.compacting',
  waiting: 'work.room.site.status.waiting',
  stopped: 'work.room.site.status.stopped',
  idle: 'work.room.site.status.idle',
}

export function siteStatusLabel(s: SiteStatus): string {
  switch (s.state) {
    case 'acting':
      return t('work.room.site.status.acting', { verb: s.verb ?? '' })
    case 'retrying':
      return s.attempt
        ? t('work.room.site.status.retryingCount', { attempt: s.attempt })
        : t('work.room.site.status.retrying')
    default:
      return t(PLAIN[s.state])
  }
}
