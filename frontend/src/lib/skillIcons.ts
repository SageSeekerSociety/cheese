/**
 * 技能 icon：认不出名字就画一颗通用拼图。
 *
 * 技能是数据驱动的（有几个技能就有几格，不是写死的几颗），所以多一个不认识的名字
 * 也只是一格画得普通，不该少一格。面板和资料页都要这张表，放这里一份。
 */
const ICONS: Record<string, string> = {
  cheese: 'mdi-cheese',
  documents: 'mdi-file-document-outline',
  showcase: 'mdi-view-dashboard-outline',
  wolfram: 'mdi-function-variant',
  'skill-creator': 'mdi-hammer-wrench',
  'mcp-builder': 'mdi-connection',
}

export function skillIcon(name: string): string {
  return ICONS[name] ?? 'mdi-puzzle-outline'
}
