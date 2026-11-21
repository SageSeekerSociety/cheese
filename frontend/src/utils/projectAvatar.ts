/**
 * 项目格子画什么：默认从项目名取字，挑过就画挑的那个（图标、表情、文字或图片）。
 *
 * 画只在 `components/common/ProjectAvatar.vue` 一处；这里是它要的几条规则。后端
 * 守同样的几条（`backend/app/domain/project/avatar.py`），这边先挡住，省一次来回。
 */
import { avatarColor, backgroundForHue } from '@/utils/avatar'

export type ProjectAvatarKind = 'icon' | 'emoji' | 'text' | 'image'

/** 后端 `ProjectAvatarOut`。kind 为 null 是默认格子；hue 为 null 是按 id 派生的颜色。 */
export interface ProjectAvatarSpec {
  kind: ProjectAvatarKind | null
  glyph: string | null
  hue: number | null
  image_id: number | null
}

/** 文字最多占这么多格：一个汉字（或假名、谚文）占两格，字母数字占一格。 */
export const TEXT_ROOM = 4

const WIDE = /[\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}\p{Script=Hangul}]/u
const WORD = /[\p{L}\p{N}]+/gu
const ALNUM = /^[\p{L}\p{N}]+$/u
const PICTOGRAPH = /\p{Extended_Pictographic}|\p{Regional_Indicator}|⃣/u

function graphemes(text: string): string[] {
  return [...new Intl.Segmenter(undefined, { granularity: 'grapheme' }).segment(text)].map((s) => s.segment)
}

function isWide(ch: string): boolean {
  return WIDE.test(ch)
}

/**
 * 默认格子上的字：项目名的第一个字母、数字或汉字，只画这一个。
 *
 * 开头的括号、标点、空格和符号照旧跳过（「【期末】操作系统实验」→ 期），拉丁字母大写
 * （「cheese」→ C）。名字里一个字母数字都没有（比如只有一个表情），取第一个字形；
 * 什么都没有画「·」。
 *
 * 到 2026-11-21 为止这一格取的是前两个字母（「Cheese」→ Ch、「Web App」→ WA），为的是
 * 分清同首字母的几个项目。那天蔡松洋定了改回一个字：这一格本来就小，两个字挤在一起
 * 不如一个字清楚；分不清的项目到项目设置里自己挑图标、表情、文字或图片。
 */
export function projectInitials(name?: string | null): string {
  const text = (name ?? '').trim()
  const words = text.match(WORD) ?? []
  if (!words.length) return graphemes(text)[0] ?? '·'
  return ([...words[0]][0] ?? '·').toUpperCase()
}

/** 文字占几格。 */
export function textRoom(text: string): number {
  return [...text].reduce((room, ch) => room + (isWide(ch) ? 2 : 1), 0)
}

/** 自定的文字放得进格子：只有字母数字，最多两个汉字或四个字母数字。 */
export function fitsTile(text: string): boolean {
  const value = text.trim()
  return !!value && ALNUM.test(value) && textRoom(value) <= TEXT_ROOM
}

/** 恰好是一个表情（肤色、国旗、组合表情都算一个）。 */
export function isOneEmoji(text: string): boolean {
  const value = text.trim()
  const parts = graphemes(value)
  return parts.length === 1 && PICTOGRAPH.test(value)
}

/** 色板：同一个色轮上隔开的几个色相，和按 id 派生的颜色同一亮度。 */
export const AVATAR_SWATCH_HUES = [12, 40, 75, 140, 175, 205, 235, 265, 300, 335] as const

/** 格子底色：挑过颜色用挑的，没挑过按项目 id 派生（改名不换色）。 */
export function projectAvatarColor(projectId: string, hue?: number | null): string {
  return hue == null ? avatarColor(projectId) : backgroundForHue(hue)
}

/**
 * 图标选单：存的名字 → 字体里的类名。类名写成字面量，图标字体的子集才扫得到它们
 * （见 scripts 下图标子集的那份测试）；不在这张表里的名字画回默认的字。
 */
export const PROJECT_ICONS: Readonly<Record<string, string>> = {
  school: 'mdi-school',
  'book-open-variant': 'mdi-book-open-variant',
  bookshelf: 'mdi-bookshelf',
  'notebook-outline': 'mdi-notebook-outline',
  'pencil-outline': 'mdi-pencil-outline',
  translate: 'mdi-translate',
  'flask-outline': 'mdi-flask-outline',
  atom: 'mdi-atom',
  dna: 'mdi-dna',
  microscope: 'mdi-microscope',
  telescope: 'mdi-telescope',
  sigma: 'mdi-sigma',
  'function-variant': 'mdi-function-variant',
  'math-integral': 'mdi-math-integral',
  'calculator-variant-outline': 'mdi-calculator-variant-outline',
  'chart-line': 'mdi-chart-line',
  'chart-bar': 'mdi-chart-bar',
  'database-outline': 'mdi-database-outline',
  'code-braces': 'mdi-code-braces',
  console: 'mdi-console',
  'language-python': 'mdi-language-python',
  'language-javascript': 'mdi-language-javascript',
  web: 'mdi-web',
  cellphone: 'mdi-cellphone',
  'robot-outline': 'mdi-robot-outline',
  brain: 'mdi-brain',
  chip: 'mdi-chip',
  server: 'mdi-server',
  'cloud-outline': 'mdi-cloud-outline',
  'cube-outline': 'mdi-cube-outline',
  'cog-outline': 'mdi-cog-outline',
  'wrench-outline': 'mdi-wrench-outline',
  'shield-outline': 'mdi-shield-outline',
  'rocket-launch-outline': 'mdi-rocket-launch-outline',
  'lightbulb-outline': 'mdi-lightbulb-outline',
  target: 'mdi-target',
  'flag-outline': 'mdi-flag-outline',
  'trophy-outline': 'mdi-trophy-outline',
  'puzzle-outline': 'mdi-puzzle-outline',
  'palette-outline': 'mdi-palette-outline',
  brush: 'mdi-brush',
  'camera-outline': 'mdi-camera-outline',
  'music-note': 'mdi-music-note',
  'movie-open-outline': 'mdi-movie-open-outline',
  'gamepad-variant-outline': 'mdi-gamepad-variant-outline',
  'newspaper-variant-outline': 'mdi-newspaper-variant-outline',
  'compass-outline': 'mdi-compass-outline',
  'map-outline': 'mdi-map-outline',
  earth: 'mdi-earth',
  leaf: 'mdi-leaf',
  'tree-outline': 'mdi-tree-outline',
  'water-outline': 'mdi-water-outline',
  fire: 'mdi-fire',
  'lightning-bolt': 'mdi-lightning-bolt',
  'heart-outline': 'mdi-heart-outline',
  'star-outline': 'mdi-star-outline',
  'account-group-outline': 'mdi-account-group-outline',
  'briefcase-outline': 'mdi-briefcase-outline',
  'bank-outline': 'mdi-bank-outline',
  'scale-balance': 'mdi-scale-balance',
  cash: 'mdi-cash',
  'home-outline': 'mdi-home-outline',
  airplane: 'mdi-airplane',
  'coffee-outline': 'mdi-coffee-outline',
}

/** 存着的图标名对应的类名；不认识的名字给空串。 */
export function projectIconClass(glyph?: string | null): string {
  return (glyph && Object.hasOwn(PROJECT_ICONS, glyph) && PROJECT_ICONS[glyph]) || ''
}
