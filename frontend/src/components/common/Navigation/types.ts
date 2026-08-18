export type NavItem = {
  key: string
  type: 'item'
  title: string
  icon?: string
  img?: string
  // Router target for navigation items. Omitted for click-action items (e.g. the
  // rail's "+" 新建项目 affordance), which run `action` instead of navigating.
  to?: string
  // Click handler for action items that have no `to` (rendered as a button, not
  // a router-link). Ignored when `to` is set.
  action?: () => void
  // A subtle "add" affordance (dashed square, mdi-plus) rather than a project tile.
  add?: boolean
  permanent?: boolean
  // Discord-style ⌘N quick-switch number shown in the hover tooltip.
  shortcut?: number
  // 这一格在哪些地址上算「正待着」。默认由 `to` 自己说了算（router-link 的
  // 规则：目标那条记录得在当前路由的 matched 里）—— 一格底下住着好几条并列的
  // 顶层路由时那条规则不够用，底栏于是整排都不亮。
  match?: (path: string) => boolean
}

export type NavDivider = {
  key: string
  type: 'divider'
}

export type NavGenericItem = NavItem | NavDivider

export type NavBarProps = {
  items: NavGenericItem[]
}
