/**
 * 外壳里那几条静态清单：底部动作面板、底栏、打开的文件，以及环境选择那两台登记的
 * 电脑。都是「摆出来给谁点」的几行，没有一个自己会动。
 *
 * 从 `../catalogFixtures.ts` 拆出来的一册。条目在 `../catalog/navigation.ts`、
 * `../catalog/panel-tabs.ts`，设备那一条在设置与市场两页。
 */
import type { RouteLocationRaw } from 'vue-router'
import type { OpenFileTab } from '@/composables/useTopicMemory'

export const SHEET_ACTIONS = [
  { key: 'rename', label: '重命名', icon: 'mdi-pencil', to: { path: '/topics/t-1/rename' } as RouteLocationRaw },
  { key: 'mute', label: '静音 1 天', icon: 'mdi-bell-off-outline', badge: '3' },
  { key: 'pin', label: '置顶', icon: 'mdi-pin-outline', loading: true },
  { key: 'archive', label: '归档', icon: 'mdi-archive-outline', disabled: true },
  { key: 'delete', label: '删除话题', icon: 'mdi-trash-can-outline', danger: true },
]

export const NAV_ITEMS = [
  { key: 'spaces', type: 'item' as const, title: '空间', icon: 'mdi-view-grid-outline', to: '/spaces' },
  { key: 'inbox', type: 'item' as const, title: '待办', icon: 'mdi-inbox-outline', to: '/inbox', badge: 3 },
  { key: 'me', type: 'item' as const, title: '我的', icon: 'mdi-account-outline', to: '/me' },
]

export const OPEN_FILES: OpenFileTab[] = [
  { path: 'README.md', pinned: false },
  { path: '.cheese/notes/plan.md', pinned: true },
]

/** 环境选择：两台登记的电脑，实验室那台是别人的，家里那台是读者自己的（离线）。 */
export const COMPUTE_DEVICES = [
  { device_id: 'lab', name: '实验室工作站', online: true, owned: false },
  { device_id: 'home', name: '家里那台', online: false, owned: true },
]
