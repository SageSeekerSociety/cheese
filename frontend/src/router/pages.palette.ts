// 命令面板里的「页面」：路由上声明了 `meta.palette` 的那几页。面板不另记一份页面
// 清单：加一页、改一页的名字，都在它自己的路由上。带 `:projectId` 的只在项目里有。
import type { RouteRecordNormalized } from 'vue-router'
import type { PaletteItem, PaletteSource, SourceContext } from '@/commands/palette/sources'

import { t } from '@/i18n'
import { DEFAULT_SHELL, shellFor, termParams } from '@/lib/shell'
import { useWorkspaceStore } from '@/stores/workspace'

function itemOf(record: RouteRecordNormalized, ctx: SourceContext): PaletteItem | null {
  const palette = record.meta.palette
  if (!palette || typeof record.name !== 'string') return null
  const inProject = record.path.includes(':projectId')
  if (inProject && !ctx.projectId) return null
  // 项目的壳能换词（「项目文档」在有的壳里叫「工作文档」），名字跟着这个项目的壳。
  const shell = shellFor(useWorkspaceStore().projects, ctx.projectId) ?? DEFAULT_SHELL
  return {
    id: `page:${record.name}`,
    title: t(palette.label, termParams(shell)),
    icon: palette.icon,
    to: { name: record.name, params: { ...(inProject ? { projectId: ctx.projectId! } : {}), ...palette.params } },
  }
}

const source: PaletteSource = {
  id: 'pages',
  label: 'navigation.palette.pages',
  order: 40,
  items: (ctx) =>
    ctx.router.getRoutes().flatMap((record) => {
      const item = itemOf(record, ctx)
      return item ? [item] : []
    }),
  fromRoute(route, ctx) {
    const record = [...route.matched].reverse().find((matched) => matched.meta.palette)
    return record ? itemOf(record, ctx) : null
  },
}

export default source
