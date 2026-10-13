// Internal to the API layer: not re-exported from `@/api`.

/** 查询串拼装。空值一律不出现 —— 发 `?q=` 和发 `?q` 对 FastAPI 的 `str | None`
 *  是两件事（后者才是「没给」）。 */
export function feedbackQuery(params: Record<string, string | number | null | undefined>): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value === null || value === undefined || value === '') continue
    search.set(key, String(value))
  }
  const qs = search.toString()
  return qs ? `?${qs}` : ''
}
