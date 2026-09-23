/** 日期窗口的两种写法：看板 KPI 深链带相对值（'7d'），日柱深链带绝对日期
 *  ('2026-09-16')。store 里存原文（chip 要拿它画「近 7 天」，地址栏里的
 *  '?since=7d' 也该原样活着——相对值的书签比绝对日期更好分享），只在发给
 *  服务端的那一刻折算：后端的三个参数是 datetime（admin_feedback.py:92-94），
 *  '7d' 在那里是 422。 */
const RELATIVE_DAYS = /^(\d{1,3})d$/

export function relativeDays(value: string): number | null {
  const m = RELATIVE_DAYS.exec(value.trim())
  return m ? Number(m[1]) : null
}

/** 发给服务端的值。相对窗口折成「n 天前的本地日期」（YYYY-MM-DD）——和看板
 *  日柱深链 `date.slice(0, 10)` 同一个形状、同一组时区语义，不比它更准也不更差。 */
export function windowToApi(value: string): string {
  const days = relativeDays(value)
  if (days === null) return value
  const at = new Date()
  at.setDate(at.getDate() - days)
  const m = String(at.getMonth() + 1).padStart(2, '0')
  const d = String(at.getDate()).padStart(2, '0')
  return `${at.getFullYear()}-${m}-${d}`
}
