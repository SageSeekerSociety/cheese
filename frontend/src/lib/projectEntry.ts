import type { RouteLocationNormalizedLoaded } from 'vue-router'

/**
 * 「你是从哪儿走进这个项目的」。
 *
 * 顶栏那颗 ← 走的是路由自己声明的父级（`meta.backTo`），而那描述的是一棵**树**。
 * 项目却是图上的一个点：同一个项目页可以从小队进、从空间进、从左边的项目栏进、
 * 从别人贴的链接进——一个写死的父级不可能对所有入口都是对的。而
 * `/projects/:projectId` 干脆一个父级都没声明，于是从小队点进去之后连 ← 都没有。
 *
 * 所以入口是**记下来**的，不是声明的：从项目**外面**走进来的那一跳记一次，在项目
 * 里怎么翻都不覆盖它——从话题按 ← 回到话题列表是框内移动，不算重新进入。
 */
export interface ProjectEntry {
  /** 目标路由名。 */
  name: string
  params: Record<string, string>
  /** 离开那一页时就抓下来的标题。回来时那一页还没加载，那一刻取不到。 */
  label: string
}

/**
 * 一个标签页一份：这是「我这次是怎么走进来的」，不是一条跨设备的偏好。换个标签页
 * 打开同一个项目，那是另一次进入，不该继承上一次的来路。
 */
const store = () => (typeof sessionStorage === 'undefined' ? null : sessionStorage)

/**
 * 按项目分别存。少了这一条，从小队进了 A 项目、再从左栏切到 B 项目，B 也会挂着
 * 「回小队」——一个它从来没有过的来路。
 */
const keyOf = (projectId: string) => `cheese:project-entry:${projectId}`

/** 这条路由是不是「项目框」里的一层（`meta.projectFrame` 由框那条记录声明）。 */
export function projectFrameOf(route: RouteLocationNormalizedLoaded): string | null {
  if (!route.matched.some((r) => r.meta?.projectFrame === true)) return null
  const id = route.params.projectId
  return typeof id === 'string' && id ? id : null
}

export function readEntry(projectId: string): ProjectEntry | null {
  const s = store()
  if (!s) return null
  const raw = s.getItem(keyOf(projectId))
  if (!raw) return null
  try {
    const parsed = JSON.parse(raw) as Partial<ProjectEntry>
    if (typeof parsed?.name !== 'string' || !parsed.name) return null
    return {
      name: parsed.name,
      params: (parsed.params ?? {}) as Record<string, string>,
      label: typeof parsed.label === 'string' ? parsed.label : '',
    }
  } catch {
    // 存坏了就当没有——落回兜底，而不是让顶栏整个炸掉。
    return null
  }
}

export function writeEntry(projectId: string, entry: ProjectEntry): void {
  store()?.setItem(keyOf(projectId), JSON.stringify(entry))
}

export function forgetEntry(projectId: string): void {
  store()?.removeItem(keyOf(projectId))
}

/**
 * 一次跳转之后，决定要不要把 `from` 记成 `to` 所在项目的入口。
 *
 * 记的条件只有一个：**这一跳是从项目外面走进来的**。
 *
 * - `from` 已经在某个项目框里 → 不记。项目之间横切（左栏切项目）是同一层上的
 *   平移，不是「从上一层走进来」；记了的话 B 项目的 ← 会指向 A 项目。
 * - `from` 没有路由名 → 不记。刷新和贴链接直接打开时 `from` 是初始位置，它不是
 *   任何人待过的地方，指过去等于把人踢出这个应用。
 */
export function recordEntry(
  to: RouteLocationNormalizedLoaded,
  from: RouteLocationNormalizedLoaded,
  label: (route: RouteLocationNormalizedLoaded) => string
): void {
  const projectId = projectFrameOf(to)
  if (!projectId) return
  if (projectFrameOf(from) !== null) return
  if (!from.name) return
  writeEntry(projectId, {
    name: String(from.name),
    params: Object.fromEntries(Object.entries(from.params).map(([k, v]) => [k, Array.isArray(v) ? v[0] ?? '' : v])),
    label: label(from),
  })
}
