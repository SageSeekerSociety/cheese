export type NavItem = {
  key: string
  type: 'item'
  title: string
  icon?: string
  img?: string
  to: string
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
