// 组件预览站自己的那点状态：地址怎么读、有哪几档设备、预览偏好是什么。
//
// 和 `catalog.ts` 分开：那一份是**注册表**（有哪个组件、每一格喂什么），是收录闸和
// 逐格挂载测试都盯着的东西；这一份是**站的骨架**（怎么找、怎么看），可以随便改。
import type { InjectionKey } from 'vue'
import type { CatalogEntry, CatalogNeed } from './catalog'
import type { MotionMode } from './demoMotion'

import { inject } from 'vue'

import { prefersReducedMotion } from './demoMotion'

/* ------------------------------------------------------------------ 地址 */

/**
 * 地址里的那几段。
 *
 *   /demo/catalog                    目录
 *   /demo/catalog/<id>               这个组件那一页
 *   /demo/catalog/<id>/frame/<n>     单组件页里的 iframe 取的那一格：只画它，不画站的
 *                                    骨架（左栏、工具条都不进 iframe）
 */
export interface CatalogRoute {
  id: string | null
  frame: number | null
}

// 结尾的 `$` 和路径边界都要有：少了它，`/demo/catalog-archive` 也会命中这条前缀，
// 被当成组件目录。入口那边的正则挡得住，但读地址这种事不该指望调用方先挡一遍。
const ROUTE = /^\/demo\/catalog(?:\/([\w-]+))?(?:\/frame\/(\d+))?$/

export function parseCatalogPath(path: string): CatalogRoute {
  const hit = ROUTE.exec(path)
  return {
    id: hit?.[1] ?? null,
    frame: hit?.[2] === undefined ? null : Number(hit[2]),
  }
}

/* --------------------------------------------------------- 站自己的那两页 */

export interface StudioPage {
  id: string
  label: string
  about: string
}

/**
 * 组件站自己带的两页：动效台和 token 页。它们不是组件，但和组件共用
 * `/demo/catalog/<id>` 这一段地址，所以这份名单**只能有一份** —— 顶栏、左栏、路由
 * 判定都读它，免得加了页面漏掉其中一处。
 *
 * 代价是组件 id 不能和这儿撞名：`demoCatalogStudio.spec.ts` 有一条测试盯着这件事。
 */
export const STUDIO_PAGES: ReadonlyArray<StudioPage> = [
  { id: 'motion', label: '动效台', about: '产品里每一种变化各摆一格，能单独重放，也能整体放慢看。' },
  { id: 'tokens', label: '设计 token', about: '产品真在用的 token：浅色深色两个色块，加一句用在哪。' },
]

/** 地址上这一段是站自己的一页吗。不是组件，也不是「目录里没有」。 */
export function reservedPage(id: string | null): StudioPage | null {
  if (id === null) return null
  return STUDIO_PAGES.find((page) => page.id === id) ?? null
}

/* ------------------------------------------------------------------ 设备 */

export type DeviceId = 'fit' | 'desktop' | 'tablet' | 'phone'

export interface Device {
  id: DeviceId
  label: string
  /** 框里的真 px 宽度。`fit` 没有宽度——跟着栏走，不裁窗口。 */
  width: number | null
}

/**
 * 三档真宽度，来自原型里的 DEV；`fit` 是本实现加的第四档。
 *
 * 前三档必须在 iframe 里按真 px 渲染：产品里按窗口宽度判断的那几条断点
 * （`useWorkspaceLayout.ts` 的 `SPLIT_MIN_WIDTH 768`、`COMPACT_DESKTOP_MAX_WIDTH 1180`，
 * Vuetify 的 `mdAndUp`）读的是 `window`/`matchMedia`，CSS `transform` 一条都改不动，
 * 在缩放的 div 里演示这些断点就是假的。
 */
export const DEVICES: ReadonlyArray<Device> = [
  { id: 'fit', label: '跟随容器', width: null },
  { id: 'desktop', label: '桌面 1280', width: 1280 },
  { id: 'tablet', label: '平板 834', width: 834 },
  { id: 'phone', label: '手机 390', width: 390 },
]

/** 并排看的那两档：宽窄差得最远的一对。 */
export const DUAL_DEVICES: ReadonlyArray<DeviceId> = ['desktop', 'phone']

export function deviceOf(id: DeviceId): Device {
  return DEVICES.find((device) => device.id === id) ?? DEVICES[0]
}

/* ------------------------------------------------------------ 预览偏好 */

export type ThemeId = 'light' | 'dark'
export type BackdropId = 'surface' | 'canvas'

export interface StudioPrefs {
  device: DeviceId
  /** 并排看宽窄两端；开着的时候 `device` 不起作用。 */
  dual: boolean
  theme: ThemeId
  motion: MotionMode
  bg: BackdropId
}

/** 工具条这一排偏好在整棵子树里共用：`DemoCatalog` 提供，舞台和单组件页取。 */
export const STUDIO: InjectionKey<StudioPrefs> = Symbol('catalog-studio')

/**
 * 取工具条那份偏好。没人在上头提供就当场炸，不要 `inject(STUDIO)!` 那样一路拖到
 * 「读 undefined 的 device」才以一句对不上号的话崩掉 —— 那两个页面本来就只有挂在
 * `DemoCatalog` 底下才成立。
 */
export function useStudio(): StudioPrefs {
  const studio = inject(STUDIO)
  if (!studio) throw new Error('组件站的那几页要挂在 DemoCatalog 底下：没有取到工具条偏好。')
  return studio
}

/** 深色/浅色的初值读的是**文档现在的**那一个（演示页可能被 `?theme=` 指定了）。 */
export function documentTheme(): ThemeId {
  if (typeof document === 'undefined') return 'light'
  return document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light'
}

/**
 * 默认这一档。
 *
 * 设备默认「跟随容器」而不是某一台设备：这一页同时也是文档嵌进 iframe 的那一页
 * （`demo-main.ts` 把 `/` 指到它上面），上来就套一个 1280 的框会把它撑破。要看设备
 * 宽度从工具条上挑一档，那几档才是 iframe 真 px。
 *
 * 动效默认这一档跟着系统的「减少动态效果」走：系统说了少动，开关落在「关」。
 */
export function defaultPrefs(): StudioPrefs {
  return {
    device: 'fit',
    dual: false,
    theme: documentTheme(),
    motion: prefersReducedMotion() ? 'off' : 'normal',
    bg: 'surface',
  }
}

/* ------------------------------------------------------------ 目录页的分组 */

export interface CatalogGroup {
  name: string
  items: CatalogEntry[]
}

const NEED_LABELS: Record<string, string> = {
  vuetify: 'Vuetify',
  i18n: '语言包',
  router: '路由',
  pinia: 'store',
}

export function needsOf(needs: CatalogNeed[]): string {
  return needs.length ? needs.map((need) => NEED_LABELS[need] ?? need).join(' · ') : '一件都不用装'
}

export function matchesCatalog(entry: CatalogEntry, words: string): boolean {
  if (!words) return true
  return [entry.title, entry.about, entry.file].some((text) => text.toLowerCase().includes(words))
}

/** 按源码目录分组，组间按目录名排、组内按标题排。和目录页上的分组是同一套。 */
export function groupCatalog(
  entries: CatalogEntry[],
  words: string,
  group: (e: CatalogEntry) => string
): CatalogGroup[] {
  const words2 = words.trim().toLowerCase()
  const byGroup = new Map<string, CatalogEntry[]>()
  for (const entry of entries) {
    if (!matchesCatalog(entry, words2)) continue
    const key = group(entry)
    byGroup.set(key, [...(byGroup.get(key) ?? []), entry])
  }
  return [...byGroup.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([name, items]) => ({ name, items: items.sort((a, b) => a.title.localeCompare(b.title)) }))
}

export function countIn(groups: CatalogGroup[]): number {
  return groups.reduce((total, group) => total + group.items.length, 0)
}

/* -------------------------------------------------------------- 舞台的地址 */

export interface StageQuery {
  theme: ThemeId
  motion: MotionMode
  bg: BackdropId
}

/**
 * 单组件页里那个设备框取哪一页。
 *
 * 地址里带主题/动效/底这三样：iframe 是**另一篇文档**，`data-theme` 得落在它自己的
 * `<html>` 上（`style.css` 的深色选择器是 `:root[data-theme='dark']`，给外面的
 * div 加属性没有用），动效改的是它自己的 `--dur-*`。
 */
export function stageHref(entryId: string, index: number, query: StageQuery): string {
  const params = new URLSearchParams({ theme: query.theme, motion: query.motion, bg: query.bg })
  return `/demo/catalog/${entryId}/frame/${index}?${params.toString()}`
}

/** iframe 里那一页把量出来的内容高度报回来用的消息标记。 */
export const FRAME_MESSAGE = 'cheese-catalog-frame'

export interface FrameMessage {
  source: typeof FRAME_MESSAGE
  height: number
}
