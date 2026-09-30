// 项目设置分四组；所有者还多一组「归档」。
//
// 这一页原来是六块竖着铺满，读的人得自己认哪块是哪块；而没绑仓库的项目从头到尾
// 只看得到跟仓库有关的东西，于是整页像是坏的 —— 队友的角色设定和模型明明是这个
// 项目的设置，却被推到另一页，页面上还写着一句「请到 AI 队友 中修改」。
//
// 所以这里问的是「哪一块在哪一组下面」，这是模板本身的事实：扫源文件，而不是把
// 整页挂起来（那要拖进 Vuetify 和十几个请求，才能断言一件标记直接写着的事）。
import { readdirSync, readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

const SRC = dirname(fileURLToPath(import.meta.url))
const view = readFileSync(join(SRC, 'views/ProjectSettingsView.vue'), 'utf8')
const SETTINGS = join(SRC, 'components/settings')

/** 组标题按它们在页面上出现的顺序。 */
function groups(): string[] {
  return [...view.matchAll(/<h2 class="t-title settings-group">([^<]+)<\/h2>/g)].map((m) => m[1])
}

/** 这一块在哪一组下面：往上找最近的那个组标题。 */
function groupOf(title: string): string {
  const at = view.indexOf(title)
  expect(at, `找不到「${title}」`).toBeGreaterThan(-1)
  const heads = [...view.slice(0, at).matchAll(/<h2 class="t-title settings-group">([^<]+)<\/h2>/g)]
  expect(heads.length, `「${title}」不在任何一组里`).toBeGreaterThan(0)
  return heads[heads.length - 1][1]
}

/** 这一块在哪一组下面。拆分（#2143）之后区块标题画在子组件里，所以先找哪个组件画
 *  了这个标题，再问这一页把那个组件放在哪一组下面。 */
function groupOfSection(title: string): string {
  const file = readdirSync(SETTINGS).find(
    (name) =>
      name.endsWith('.vue') &&
      readFileSync(join(SETTINGS, name), 'utf8').includes(`<span class="page-section-title">${title}</span>`)
  )
  expect(file, `没有哪个设置组件画「${title}」`).toBeTruthy()
  return groupOf(`<${String(file).replace(/\.vue$/, '')}`)
}

describe('项目设置', () => {
  it('分成五组，顺序从「最常改的」到「接一次就不动的」，归档在最后', () => {
    expect(groups()).toEqual(['队友', '工作电脑', '交付', '仓库', '归档'])
  })

  it('归档自成一组，只画给所有者', () => {
    expect(groupOf('<ArchiveProjectSection')).toBe('归档')
    expect(view).toMatch(/<template v-if="ownsProject">\s*<h2 class="t-title settings-group">归档<\/h2>/)
  })

  it('队友在第一组——它是没绑仓库的项目唯一要改的东西', () => {
    expect(groupOf('<AgentTeamSettings')).toBe('队友')
  })

  it('额度跟着工作电脑走：它答的是「还能跑多久」', () => {
    expect(groupOf('<CreditsPanel')).toBe('工作电脑')
  })

  it('分支保护是交付规则，不是仓库连接', () => {
    expect(groupOfSection('分支保护')).toBe('交付')
  })

  it('仓库组包含托管状态、GitHub 仓库地址与连接设置', () => {
    expect(groupOfSection('代码仓库')).toBe('仓库')
    expect(groupOfSection('GitHub 仓库地址')).toBe('仓库')
    expect(groupOfSection('连接 GitHub 仓库')).toBe('仓库')
    expect(groupOfSection('连接 GitHub 账号')).toBe('仓库')
  })

  it('MCP 服务器跟着仓库走：清单读自仓库里的 .mcp.json', () => {
    expect(groupOf('<ProjectMcpSettings')).toBe('仓库')
  })

  it('页头不再把人指去别的页面改角色设定', () => {
    expect(view).not.toContain('请到')
  })
})
