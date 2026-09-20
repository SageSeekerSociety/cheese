// 项目设置分四组。
//
// 这一页原来是六块竖着铺满，读的人得自己认哪块是哪块；而没绑仓库的项目从头到尾
// 只看得到跟仓库有关的东西，于是整页像是坏的 —— 队友的角色设定和模型明明是这个
// 项目的设置，却被推到另一页，页面上还写着一句「请到 AI 队友 中修改」。
//
// 所以这里问的是「哪一块在哪一组下面」，这是模板本身的事实：扫源文件，而不是把
// 整页挂起来（那要拖进 Vuetify 和十几个请求，才能断言一件标记直接写着的事）。
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

const SRC = dirname(fileURLToPath(import.meta.url))
const view = readFileSync(join(SRC, 'views/ProjectSettingsView.vue'), 'utf8')

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

describe('项目设置', () => {
  it('分成四组，顺序从「最常改的」到「接一次就不动的」', () => {
    expect(groups()).toEqual(['队友', '运行环境', '交付', '仓库'])
  })

  it('队友在第一组——它是没绑仓库的项目唯一要改的东西', () => {
    expect(groupOf('<AgentTeamSettings')).toBe('队友')
  })

  it('额度跟着运行环境走：它答的是「还能跑多久」', () => {
    expect(groupOf('<CreditsPanel')).toBe('运行环境')
  })

  it('分支保护是交付规则，不是仓库连接', () => {
    expect(groupOf('>分支保护<')).toBe('交付')
  })

  it('仓库那一组里是三块连接：上游、GitHub 仓库、GitHub 账号', () => {
    expect(groupOf('>上游仓库<')).toBe('仓库')
    expect(groupOf('>连接 GitHub 仓库<')).toBe('仓库')
    expect(groupOf('>连接 GitHub 账号<')).toBe('仓库')
  })

  it('页头不再把人指去别的页面改角色设定', () => {
    expect(view).not.toContain('请到')
  })
})
