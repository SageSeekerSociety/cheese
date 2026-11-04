// 「新建」那一页上的模板：一条模板是「填表的起点」，选中它就把那几格铺进那张表。
//
// 所以模板带的就是表单要的那几样（目标、触发方式、时间或条件的 `spec`），不另立一套
// 说法：预置在这里，项目里存下来的那几条从后端读出来（同一份形状），两边进同一个列表。
//
// 「什么时候跑」那一行由 `routineWhenText` 现算，不存成一句话：存下来的那几条也要跟着
// 读者换语言，存一句中文进库，英文界面上就永远是一行中文。
import type { RoutineTrigger } from './routine'

import { t } from '@/i18n'

export interface RoutineTemplate {
  /** 列表里的键。预置是这里起的名字，存下来的那几条是后端给的 id。 */
  id: string
  title: string
  /** 铺进「目标」那一格的话。 */
  goal: string
  /** 有补充说明的就一起铺进去。 */
  note?: string
  trigger: RoutineTrigger | ''
  /** 时间那几格（`trigger` 是 `schedule` 时才有），或者条件那几条带的 `scope`。 */
  spec: Record<string, unknown>
}

/** 存一条模板时给后端的那几格：同一份形状，`id` 由后端给，名字可以省（省了从目标里取）。 */
export interface RoutineTemplateInput {
  title?: string
  goal: string
  note?: string
  /** '' 是「还没定什么时候跑」：照它建出来的规则先停着。 */
  trigger: RoutineTrigger | ''
  spec: Record<string, unknown>
}

const WEEKDAY_KEYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'] as const

/** 「每周一 09:00」那样的一句话：这一条什么时候跑。 */
export function routineWhenText(trigger: RoutineTrigger | '', spec: Record<string, unknown>): string {
  // 存下来的模板可以没有触发方式：那一行就只有目标，不另造一句话。
  if (!trigger) return ''
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

/** 平台预置的五条：一眼看得懂什么时候跑、跑完交出什么。
 *
 *  每套都带着一段「补充说明」，那是做法——列表上只摆名字、什么时候跑和一句目标是干什么的，
 *  点开一套，做法才铺进表里给人看、给人改。 */
export function presetTemplates(): RoutineTemplate[] {
  const said = (key: string) => ({
    id: `preset-${key}`,
    title: t(`routines.template.presets.${key}.title`),
    goal: t(`routines.template.presets.${key}.goal`),
    note: t(`routines.template.presets.${key}.note`),
  })
  return [
    { ...said('weekly'), trigger: 'schedule', spec: { freq: 'weekly', time: '09:00', weekdays: [0] } },
    { ...said('quiz'), trigger: 'schedule', spec: { freq: 'weekly', time: '15:00', weekdays: [4] } },
    { ...said('daily'), trigger: 'schedule', spec: { freq: 'daily', time: '18:00' } },
    { ...said('archive'), trigger: 'library_file_added', spec: { scope: 'room' } },
    { ...said('unit'), trigger: 'task_closed', spec: { scope: 'room' } },
  ]
}
