/**
 * 这个人自己对项目侧栏做过的两种选择：手动展开过的页，和亲手从侧栏上拿掉的页。
 *
 * 壳能决定哪些页**默认收起**，不能决定一个人永远打不开它——收起的语义是「收起」，
 * 不是「禁止」。所以打开过一次就记住，而个人级压过壳：课程壳把「成员」收起来了，
 * 一个天天看名册的成员打开一次，之后它就在他自己的侧栏里。
 *
 * 反过来也一样：壳摆出来的页，他也能从自己的侧栏上拿掉（FB-49）。拿掉的页仍然在
 * 项目菜单里，一次点击可达；而且「拿掉」压过「打开过一次」——他从菜单里点开一个
 * 亲手拿掉的页，是这一次要用它，不是要它回到侧栏上，否则拿掉这件事只撑到下一次
 * 打开为止。回到侧栏只有一条路：在菜单里那一格上点「在侧栏显示」。
 *
 * 按 handle 存，和 projectOrder 同一个理由：这是**这个人**的偏好。整个浏览器一份
 * 的话，换个账号进来看到的是上一个人展开过的东西。
 *
 * 一个人一份、不分项目：在课程项目里展开过名册，在另一个课程项目里也该是展开的
 * ——这是一个人对一个**壳**的选择，不是对某一个项目的数据。
 */
const REVEALED_PREFIX = 'cheesex.shellRevealed.v1:'
const CONCEALED_PREFIX = 'cheesex.shellConcealed.v1:'

function key(prefix: string, handle: string): string | null {
  const normalized = handle.trim()
  return normalized ? `${prefix}${encodeURIComponent(normalized)}` : null
}

function load(prefix: string, handle: string): Set<string> {
  const storageKey = key(prefix, handle)
  if (!storageKey || typeof localStorage === 'undefined') return new Set()
  try {
    const parsed: unknown = JSON.parse(localStorage.getItem(storageKey) || '[]')
    if (!Array.isArray(parsed)) return new Set()
    return new Set(parsed.filter((k): k is string => typeof k === 'string'))
  } catch {
    return new Set() // 存坏了就当没选过，壳的默认照样能用
  }
}

function save(prefix: string, handle: string, pages: ReadonlySet<string>): void {
  const storageKey = key(prefix, handle)
  if (!storageKey || typeof localStorage === 'undefined') return
  try {
    localStorage.setItem(storageKey, JSON.stringify([...pages]))
  } catch {
    // 存不下（隐私模式、配额满）就只有这一次会话算数。
  }
}

export function loadRevealedPages(handle: string): Set<string> {
  return load(REVEALED_PREFIX, handle)
}

/** 他亲手从侧栏上拿掉的页（`project-docs` 是项目文档那一行）。 */
export function loadConcealedPages(handle: string): Set<string> {
  return load(CONCEALED_PREFIX, handle)
}

/** 记下「这一页他打开过」，返回新的集合。 */
export function withRevealedPage(revealed: ReadonlySet<string>, page: string, handle: string): Set<string> {
  if (revealed.has(page)) return new Set(revealed)
  const next = new Set(revealed)
  next.add(page)
  save(REVEALED_PREFIX, handle, next)
  return next
}

/**
 * 把一页放上侧栏（`onRail = true`）或从侧栏上拿掉，返回新的两份集合。两份一起改：
 * 拿掉一页时也把「打开过」那条记录去掉，放回来时也把「拿掉」那条去掉——两条同时
 * 在场的话，谁说了算就得另立一条规矩。
 */
export function withPageOnRail(
  prefs: { revealed: ReadonlySet<string>; concealed: ReadonlySet<string> },
  page: string,
  onRail: boolean,
  handle: string
): { revealed: Set<string>; concealed: Set<string> } {
  const revealed = new Set(prefs.revealed)
  const concealed = new Set(prefs.concealed)
  if (onRail) {
    revealed.add(page)
    concealed.delete(page)
  } else {
    revealed.delete(page)
    concealed.add(page)
  }
  save(REVEALED_PREFIX, handle, revealed)
  save(CONCEALED_PREFIX, handle, concealed)
  return { revealed, concealed }
}
