// Compact relative time for list rows: 刚刚 / 5分钟前 / 3小时前 / 昨天 / 4天前
// / 06-12. Deterministic formatting, no library.
export function relTime(iso: string | null | undefined): string {
  if (!iso) return ''
  const t = new Date(iso).getTime()
  if (Number.isNaN(t)) return ''
  const s = (Date.now() - t) / 1000
  if (s < 60) return '刚刚'
  if (s < 3600) return `${Math.floor(s / 60)}分钟前`
  if (s < 86400) return `${Math.floor(s / 3600)}小时前`
  if (s < 172800) return '昨天'
  if (s < 604800) return `${Math.floor(s / 86400)}天前`
  const d = new Date(t)
  const mm = String(d.getMonth() + 1).padStart(2, '0')
  const dd = String(d.getDate()).padStart(2, '0')
  return `${mm}-${dd}`
}
