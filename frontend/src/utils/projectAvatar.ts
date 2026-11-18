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
 * 默认格子上的字。跳过开头的括号、标点、空格和符号：
 * - 第一个字是汉字（或假名、谚文）就只取这一个字；
 * - 否则是拉丁字母或数字：名字有两个以上的词，取前两个词的首字母（「Web App」→ WA，
 *   「Lab 2」→ L2），这样只差后一个词的几个项目也分得开；只有一个词，取它的前两个
 *   字母（「Cheese」→ Ch）。
 * 名字里一个字母数字都没有（比如只有一个表情），取第一个字形；什么都没有画「·」。
 */
export function projectInitials(name?: string | null): string {
  const text = (name ?? '').trim()
  const words = text.match(WORD) ?? []
  if (!words.length) return graphemes(text)[0] ?? '·'
  const [first, second] = words
  const head = [...first]
  if (isWide(head[0])) return head[0]
  if (second && !isWide(second[0])) return (head[0] + [...second][0]).toUpperCase()
  // 一个词里拉丁字母接着汉字（「AI助手」）：只取汉字之前那一段。
  const latin = head.slice(0, 2).filter((ch, i) => i === 0 || !isWide(ch))
  return latin[0].toUpperCase() + (latin[1] ?? '')
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

/** 图标选单：mdi 字体里的名字（不带 `mdi-`）。 */
export const PROJECT_ICONS = [
  'school',
  'book-open-variant',
  'bookshelf',
  'notebook-outline',
  'pencil-outline',
  'translate',
  'flask-outline',
  'atom',
  'dna',
  'microscope',
  'telescope',
  'sigma',
  'function-variant',
  'math-integral',
  'calculator-variant-outline',
  'chart-line',
  'chart-bar',
  'database-outline',
  'code-braces',
  'console',
  'language-python',
  'language-javascript',
  'web',
  'cellphone',
  'robot-outline',
  'brain',
  'chip',
  'server',
  'cloud-outline',
  'cube-outline',
  'cog-outline',
  'wrench-outline',
  'shield-outline',
  'rocket-launch-outline',
  'lightbulb-outline',
  'target',
  'flag-outline',
  'trophy-outline',
  'puzzle-outline',
  'palette-outline',
  'brush',
  'camera-outline',
  'music-note',
  'movie-open-outline',
  'gamepad-variant-outline',
  'newspaper-variant-outline',
  'compass-outline',
  'map-outline',
  'earth',
  'leaf',
  'tree-outline',
  'water-outline',
  'fire',
  'lightning-bolt',
  'heart-outline',
  'star-outline',
  'account-group-outline',
  'briefcase-outline',
  'bank-outline',
  'scale-balance',
  'cash',
  'home-outline',
  'airplane',
  'coffee-outline',
] as const
