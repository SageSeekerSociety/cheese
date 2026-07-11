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
  visibleOnMobile?: boolean
  visibleOnPC?: boolean
  // Discord-style ⌘N quick-switch number shown in the hover tooltip.
  shortcut?: number
}

export type NavDivider = {
  key: string
  type: 'divider'
}

export type NavGenericItem = NavItem | NavDivider

export type NavBarProps = {
  items: NavGenericItem[]
}
