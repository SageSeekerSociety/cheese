/**
 * 这个人在壳里手动展开过的项目页。
 *
 * 壳能决定哪些页**默认收起**，不能决定一个人永远打不开它——收起的语义是「收起」，
 * 不是「禁止」。所以打开过一次就记住，而个人级压过壳：课程壳把「日历」收起来了，
 * 一个天天用日历的学生打开一次，之后它就在他自己的侧栏里。
 *
 * 按 handle 存，和 projectOrder 同一个理由：这是**这个人**的偏好。整个浏览器一份
 * 的话，换个账号进来看到的是上一个人展开过的东西。
 *
 * 一个人一份、不分项目：在课程项目里展开过日历，在另一个课程项目里也该是展开的
 * ——这是一个人对一个**壳**的选择，不是对某一个项目的数据。
 */
const REVEALED_PREFIX = 'cheesex.shellRevealed.v1:'

function key(handle: string): string | null {
  const normalized = handle.trim()
  return normalized ? `${REVEALED_PREFIX}${encodeURIComponent(normalized)}` : null
}

export function loadRevealedPages(handle: string): Set<string> {
  const storageKey = key(handle)
  if (!storageKey || typeof localStorage === 'undefined') return new Set()
  try {
    const parsed: unknown = JSON.parse(localStorage.getItem(storageKey) || '[]')
    if (!Array.isArray(parsed)) return new Set()
    return new Set(parsed.filter((k): k is string => typeof k === 'string'))
  } catch {
    return new Set() // 存坏了就当没展开过，壳的默认照样能用
  }
}

/** 记下「这一页他打开过」，返回新的集合。 */
export function withRevealedPage(revealed: ReadonlySet<string>, page: string, handle: string): Set<string> {
  if (revealed.has(page)) return new Set(revealed)
  const next = new Set(revealed)
  next.add(page)
  const storageKey = key(handle)
  if (storageKey && typeof localStorage !== 'undefined') {
    try {
      localStorage.setItem(storageKey, JSON.stringify([...next]))
    } catch {
      // 存不下（隐私模式、配额满）就只有这一次会话算数：这一页这一趟是展开的。
    }
  }
  return next
}
