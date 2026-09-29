// 命令面板能找到的东西从哪来。
//
// 面板本身不认识任何一样具体的东西（话题、成员、资料库那一页）。每一类由一个数据源
// 交出来：一个以 `.palette.ts` 结尾、默认导出一个 PaletteSource 的文件，放在它所属的
// 那块代码旁边。面板启动时把它们全部收上来，加一类东西不用改面板，也不用改一张清单。
//
// 数据源只交候选，不做匹配：拼音首字母、打分、分组、「最近去过」都在面板里统一做。
import type { RouteLocationNormalizedLoaded, RouteLocationRaw, Router } from 'vue-router'

export type Prefix = '#' | '@' | '>'

export interface PaletteItem {
  /** 全面板唯一且稳定，形如 `topic:<id>`：「最近去过」靠它认。 */
  id: string
  title: string
  /** 名字下面那一行：成员的 @handle、操作属于哪一页。 */
  subtitle?: string
  /** mdi 图标名。 */
  icon: string
  /** 行尾的一小段状态（「已归档」）。`warn` 是需要这个人去做点什么的。 */
  badge?: { text: string; tone?: 'warn' }
  /** 按它也能找到的别名。 */
  keywords?: string[]
  /** 这一条在等这个人（等他验收、等他回答）：不打字时排在最上面。 */
  awaiting?: boolean
  /** 快捷键，放在 title 里提示，不常驻界面。 */
  shortcut?: string
  to?: RouteLocationRaw
  run?: () => void
}

export interface SourceContext {
  /** 当前在哪个项目里；不在项目里时是 null，只在项目里有的几类就不出现。 */
  projectId: string | null
  router: Router
}

export interface PaletteSource {
  id: string
  /** 结果里这一组的名字（i18n key）。 */
  label: string
  /** 组的先后，小的在前。 */
  order: number
  /** 输入以它开头时只看这一组。 */
  prefix?: Prefix
  items: (ctx: SourceContext) => PaletteItem[]
  /** 这个地址是不是这一类里的某一条：从别处（侧栏、链接）去过的地方也算「最近去过」。 */
  fromRoute?: (route: RouteLocationNormalizedLoaded, ctx: SourceContext) => PaletteItem | null
}

const modules = import.meta.glob<PaletteSource>('/src/**/*.palette.ts', { eager: true, import: 'default' })

export const paletteSources: PaletteSource[] = Object.values(modules).sort((a, b) => a.order - b.order)
