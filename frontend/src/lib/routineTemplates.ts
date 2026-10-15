// 「新建」那一页上的模板：一条模板是「填表的起点」，选中它就把那几格铺进那张表。
//
// 所以模板带的就是表单要的那几样（目标、触发方式、时间或条件的 `spec`），不另立一套
// 说法：预置在这里，用户自己存下来的那几条由调用方从浏览器里读出来（`RoutineTemplate`
// 是同一份形状），两边进同一个列表。
//
// 「什么时候跑」那一行由 `routineWhenText` 现算，不存成一句话：存下来的那几条也要跟着
// 读者换语言，存一句中文进浏览器，英文界面上就永远是一行中文。
import type { RoutineTrigger } from './routine'

import { t } from '@/i18n'

export interface RoutineTemplate {
  /** 列表里的键。预置是这里起的名字，用户存的那几条是存下来时给的 id。 */
  id: string
  title: string
  /** 铺进「目标」那一格的话。 */
  goal: string
  /** 有补充说明的（人自己存下来的那几条可能带着）就一起铺进去。 */
  note?: string
  trigger: RoutineTrigger
  /** 时间那几格（`trigger` 是 `schedule` 时才有），或者条件那几条带的 `scope`。 */
  spec: Record<string, unknown>
}

const WEEKDAY_KEYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'] as const

/** 「每周一 09:00」那样的一句话：这一条什么时候跑。 */
export function routineWhenText(trigger: RoutineTrigger, spec: Record<string, unknown>): string {
  if (trigger !== 'schedule') return t(`routines.trigger.${trigger}`)
  const freq = String(spec.freq ?? 'daily')
  const time = String(spec.time ?? '09:00')
  if (freq === 'hourly') return t('routines.template.when.hourly', { minute: Number(spec.minute ?? 0) })
  if (freq === 'weekly') {
    const days = Array.isArray(spec.weekdays) ? (spec.weekdays as number[]) : [0]
    const named = days.map((d) => t(`routines.weekday.${WEEKDAY_KEYS[d] ?? 'mon'}`)).join(t('routines.listSeparator'))
    return t('routines.template.when.weekly', { days: named, time })
  }
  if (freq === 'monthly') return t('routines.template.when.monthly', { day: Number(spec.day ?? 1), time })
  return t('routines.template.when.daily', { time })
}

/** 平台预置的五条：一眼看得懂什么时候跑、跑完交出什么。 */
export function presetTemplates(): RoutineTemplate[] {
  const said = (key: string) => ({
    id: `preset-${key}`,
    title: t(`routines.template.presets.${key}.title`),
    goal: t(`routines.template.presets.${key}.goal`),
  })
  return [
    { ...said('weekly'), trigger: 'schedule', spec: { freq: 'weekly', time: '09:00', weekdays: [0] } },
    { ...said('quiz'), trigger: 'schedule', spec: { freq: 'weekly', time: '15:00', weekdays: [4] } },
    { ...said('daily'), trigger: 'schedule', spec: { freq: 'daily', time: '18:00' } },
    { ...said('archive'), trigger: 'library_file_added', spec: { scope: 'room' } },
    { ...said('unit'), trigger: 'task_closed', spec: { scope: 'room' } },
  ]
}
