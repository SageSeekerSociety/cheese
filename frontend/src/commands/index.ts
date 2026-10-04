// 命令：一件能做的事，只写一次。
//
// 页头上的按钮、手机顶栏右边那一颗和它的 ⋯、键盘快捷键，读的都是同一张表，所以
// 一件事不在每一处各写一遍，也不会一处改了另一处没改。「对某个东西能做什么」（一个
// 话题的重命名、归档）不在这张表里：那是一个普通函数，谁要谁调，见 topicActions.ts。
//
// 一个组件在 setup 里登记这一页此刻能做的事：
//
//   useCommands(() => [
//     { id: 'library.upload', title: '上传文件', icon: 'mdi-upload',
//       header: { primary: true, accent: true }, run: pickFile },
//   ])
//
// 传进去的是一个函数，读表的人每次都重新调用它，所以标题、loading、disabled 都跟着
// 组件里的状态走。组件挂上时登记，卸下时撤掉；被 keep-alive 收起来的页面停用时也撤。
import type { RouteLocationRaw } from 'vue-router'
import type { MenuAction } from '@/components/common/menuAction'

import { computed, getCurrentInstance, onActivated, onBeforeUnmount, onDeactivated, shallowRef } from 'vue'

interface CommandBase {
  /** 全应用唯一且不变：快捷键、以后的「最近用过」都靠它认这一条。 */
  id: string
  title: string
  /** mdi 图标名。 */
  icon?: string
  /** `mod+1`、`mod+shift+f`。mod 在 Mac 上是 ⌘，别处是 Ctrl。按物理键认，不跟键盘布局走。
   *  中间是空格的是序列键：`g 1` = 先按 G、一秒内再按 1（输入框里不认）。 */
  shortcut?: string
  /** 退出、删除这类：菜单里那一行用 --danger-ink。 */
  danger?: boolean
  disabled?: boolean
  loading?: boolean
  /** 做这件事就是去一个地方。和 run 可以同时给，先跑 run。 */
  to?: RouteLocationRaw
  run?: () => void
  /**
   * 不进命令面板。「刷新」这种只在它那一页有意义、在面板里分不清是哪一页的；切项目
   * 的 ⌘1–9 在面板里已经由「项目」那一组给出。
   */
  palette?: false
}

/**
 * 这一页的页头上要不要有它。桌面上是页名右边的一颗按钮；手机上页头不画，`primary`
 * 的那一条是顶栏右边一颗图标，其余进顶栏的 ⋯。
 */
export interface HeaderPlacement {
  /** 手机顶栏上那一颗。一页只标一条；标了几条只取第一条。 */
  primary?: boolean
  /** 桌面上画成琥珀色实心按钮：这一页的主操作（设计系统 §1.6）。 */
  accent?: boolean
  /** 桌面上也只画图标，字进 aria-label。 */
  iconOnly?: boolean
}

/** 上页头的命令必须有图标：手机顶栏上只画图标。 */
export type Command = CommandBase & ({ header?: undefined } | { header: HeaderPlacement; icon: string })
export type HeaderCommand = Extract<Command, { header: HeaderPlacement }>

/** 能进菜单的命令：菜单和底部面板里每一行都有图标。 */
export type MenuCommand = Command & { icon: string }

/** 一条命令在菜单（AdaptiveMenu / MobileActionSheet）里的那一行。 */
export function menuActionOf(command: MenuCommand): MenuAction {
  return {
    key: command.id,
    label: command.title,
    icon: command.icon,
    danger: command.danger,
    loading: command.loading,
    disabled: command.disabled,
    to: command.to,
    onSelect: command.run,
  }
}

// 模块级的一张表，不进 pinia：页面组件的单测不必为此装一个 store。
//
// 每个登记者各有一个号，各自撤各自的。换页时新页的登记和旧页的撤销谁先谁后不一定，
// 「全部清空」会把新页刚登记的也清掉。按号排：号是组件建出来的顺序，也就是它们在
// 模板里的顺序。
const entries = shallowRef<{ id: number; commands: () => Command[] }[]>([])
let nextId = 1

/** 此刻所有能做的事。读的时候才调各家的函数，所以是活的。 */
export const activeCommands = computed(() => entries.value.flatMap((entry) => entry.commands()))

/** 这一页页头上的那几条。 */
export const headerCommands = computed(() =>
  activeCommands.value.filter((command): command is HeaderCommand => command.header !== undefined)
)

function register(id: number, commands: () => Command[]) {
  const rest = entries.value.filter((entry) => entry.id !== id)
  entries.value = [...rest, { id, commands }].sort((a, b) => a.id - b.id)
}

function unregister(id: number) {
  if (entries.value.some((entry) => entry.id === id)) entries.value = entries.value.filter((entry) => entry.id !== id)
}

/**
 * 在组件的 setup 里调用：组件在屏幕上时，这些事就能做。
 *
 * setup 里当场登记，不等挂载：页头是这个组件的子组件，要在第一次渲染时就读到这几
 * 条，不然第一帧页头是空的，下一帧按钮才冒出来。
 */
export function useCommands(commands: () => Command[]) {
  if (!getCurrentInstance()) throw new Error('useCommands must be called from a component setup()')
  const id = nextId++
  register(id, commands)
  onActivated(() => register(id, commands))
  onDeactivated(() => unregister(id))
  onBeforeUnmount(() => unregister(id))
}

/** 不属于哪一页的事（切项目的 ⌘1–9）：一直在，直到调用返回的函数。 */
export function defineCommands(commands: () => Command[]): () => void {
  const id = nextId++
  register(id, commands)
  return () => unregister(id)
}
