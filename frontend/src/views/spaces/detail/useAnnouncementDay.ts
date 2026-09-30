// 公告上的日子怎么念：「9 月 28 日」，不是今年的带上年份。发布日期、到期日，公告页
// 和题目列表顶上那一栏都用这一句。
import type { AnnouncementDay } from '../model'

import { useI18n } from 'vue-i18n'

export function useAnnouncementDay() {
  const { t } = useI18n()
  return (day: AnnouncementDay): string =>
    day.year === null
      ? t('spaces.announcements.day', { month: day.month, date: day.date })
      : t('spaces.announcements.dayWithYear', { year: day.year, month: day.month, date: day.date })
}
