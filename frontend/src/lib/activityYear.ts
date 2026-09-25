// 个人主页那张一年的活动图：365 个 UTC 日排成「一列一周、一行一个星期几」。
//
// 日子是后端按 UTC 数的（和平台其余的日图一样），所以这里所有的日期运算都在
// UTC 上做：一个 `YYYY-MM-DD` 被当成那一天的 UTC 零点，从不经过本地时区——否则
// 西半球的人会看见每一格都往前挪了一天。
//
// 一周从星期一开始，和国内的日历一致。第一列和最后一列通常不满：年初那几天前面、
// 今天后面空出来的格子是 `null`，画成空位而不是零。

import type { ProfileActivityDay } from '@/cx_types'

export interface ActivityCell {
  date: string
  count: number
  /** 0 = 这一天没有贡献，1–4 = 由少到多。 */
  level: 0 | 1 | 2 | 3 | 4
}

export interface ActivityWeek {
  /** 这一周的星期一与星期日（UTC 日期），选中一周时就按这两天去问话题。 */
  from: string
  to: string
  /** 七格，星期一在前；窗口以外的日子是 null。 */
  cells: (ActivityCell | null)[]
  total: number
}

const DAY_MS = 86_400_000

function parseDay(day: string): number {
  return Date.parse(`${day}T00:00:00Z`)
}

function formatDay(ms: number): string {
  return new Date(ms).toISOString().slice(0, 10)
}

/** 0 = 星期一 … 6 = 星期日。 */
function weekdayOf(ms: number): number {
  return (new Date(ms).getUTCDay() + 6) % 7
}

/**
 * 深浅四档按「有贡献的那些天」的四分位切，而不是按最大值等分：一个人某天写了
 * 两百条，按最大值切的话其余每一天都落在最浅的那一档，整张图看起来是空的。
 */
function thresholds(counts: number[]): [number, number, number] {
  const active = counts.filter((n) => n > 0).sort((a, b) => a - b)
  if (active.length === 0) return [1, 1, 1]
  const at = (q: number) => active[Math.min(active.length - 1, Math.floor(q * active.length))]
  return [at(0.25), at(0.5), at(0.75)]
}

function levelOf(count: number, [q1, q2, q3]: [number, number, number]): ActivityCell['level'] {
  if (count <= 0) return 0
  if (count <= q1) return 1
  if (count <= q2) return 2
  if (count <= q3) return 3
  return 4
}

/**
 * 把日子排成周。`lastWeeks` 只留最近的那几周（手机上半年一张图）；深浅档位仍按
 * 传进来的全部日子算，截掉一半不会让同一天换一种深浅。
 */
export function activityWeeks(days: ProfileActivityDay[], lastWeeks?: number): ActivityWeek[] {
  if (days.length === 0) return []
  const cut = thresholds(days.map((d) => d.count))
  const first = parseDay(days[0].date)
  const start = first - weekdayOf(first) * DAY_MS
  const byDay = new Map(days.map((d) => [d.date, d.count]))
  const last = parseDay(days[days.length - 1].date)

  const weeks: ActivityWeek[] = []
  for (let monday = start; monday <= last; monday += 7 * DAY_MS) {
    const cells: (ActivityCell | null)[] = []
    let total = 0
    for (let i = 0; i < 7; i++) {
      const date = formatDay(monday + i * DAY_MS)
      const count = byDay.get(date)
      if (count === undefined) {
        cells.push(null)
        continue
      }
      total += count
      cells.push({ date, count, level: levelOf(count, cut) })
    }
    weeks.push({ from: formatDay(monday), to: formatDay(monday + 6 * DAY_MS), cells, total })
  }
  return lastWeeks === undefined ? weeks : weeks.slice(-lastWeeks)
}

/**
 * 月份标在它第一天所在的那一列上。离上一个标签不到三列的不标：年初那个不满的月
 * 份只占一两列，两个标签会叠在一起。
 */
export function monthStarts(weeks: ActivityWeek[]): { column: number; month: number }[] {
  const marks: { column: number; month: number }[] = []
  weeks.forEach((week, column) => {
    const firstOfMonth = week.cells.find((cell) => cell && cell.date.endsWith('-01'))
    const opening = column === 0 ? week.cells.find((cell) => cell) : firstOfMonth
    if (!opening) return
    const month = Number(opening.date.slice(5, 7))
    const previous = marks[marks.length - 1]
    if (previous && column - previous.column < 3) {
      // 年初那个只露出一两列的月份让位给下一个整月。
      if (previous.column === 0) marks.pop()
      else return
    }
    marks.push({ column, month })
  })
  return marks
}

/** `YYYY-MM-DD` 按 UTC 那一天格式化（「9月15日」/「Sep 15」）。 */
export function formatUtcDay(day: string, locale: string, options: Intl.DateTimeFormatOptions): string {
  return new Intl.DateTimeFormat(locale, { ...options, timeZone: 'UTC' }).format(new Date(parseDay(day)))
}
