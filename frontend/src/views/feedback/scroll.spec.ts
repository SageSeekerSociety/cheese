/** 反馈页面必须自己领滚动（三个；原先还有第四个 /design/feedback，已删）。
 *
 * 全站约定：`styles/common.scss` 把 `html / body / #app` 定成固定高度加
 * `overflow: hidden`，**滚动由每一页自己领** —— 别的页面都写着
 * `<div class="overview-page fill-height overflow-y-auto">` 这一组（OverviewView、
 * ProfileView、MarketView、ProjectDocsView…十来个）。
 *
 * 反馈这几页当初只写了页面自己的类。于是内容比窗口高的时候，下半截被外壳那层
 * `overflow-hidden` 裁掉，而页面上**没有任何元素能滚**：1280×600 的窗口里反馈中心
 * 有 117px 永远够不着（窗口高到 720 正好放得下，所以一直没露出来）。
 *
 * 这是 CSS，jsdom 量不到布局，照仓库的老办法用源码断言钉住（见
 * `components/ChatPanel.mentionMenu.spec.ts`）。注释里也会出现这两个类名，先摘掉
 * 再断言，免得我自己写的注释把测试骗绿。
 *
 * **管理端那一页现在是例外**，而且是有意的：它不再让整页一起滚，改成
 * `height: 100%` 的纵向 flex，把头（标题 / 选项卡 / 工具栏）和脚（翻页）钉住，
 * **滚动交给表格自己**（`AdminGrid` 的 `.agrid__scroll`）。所以「这一页有没有人能滚」
 * 这个不变量仍然成立，只是领滚动的元素从根节点下移了一层——下面两个用例分别
 * 钉这两件事，谁也别按谁的样子改。
 */
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

const dir = dirname(fileURLToPath(import.meta.url))
const srcDir = join(dir, '..', '..')

function read(rel: string): string {
  return readFileSync(join(srcDir, rel), 'utf8')
}

/** `<template>` 里第一个元素 —— 也就是这个页面的根节点。多元素会直接报出来，
 *  因为 `buildRoot` 只取第一行，那种模板本来也不是这一页该有的样子。 */
function rootTag(page: string): string {
  const src = readFileSync(join(dir, `${page}.vue`), 'utf8')
  const open = src.indexOf('<template>')
  const close = src.indexOf('</template>', open)
  const body = src.slice(open + '<template>'.length, close).replace(/<!--[\s\S]*?-->/g, '')
  return body.trim().split('\n')[0]
}

/** 一个 SFC 里某条选择器的声明块，用来断言 CSS 本身。注释先摘掉。 */
function cssRule(rel: string, selector: string): string {
  const src = read(rel).replace(/\/\*[\s\S]*?\*\//g, '')
  const at = src.indexOf(`${selector} {`)
  if (at < 0) throw new Error(`${rel} 里找不到 ${selector}`)
  return src.slice(at, src.indexOf('}', at))
}

// 「我的反馈」是重做之后才补进这份清单的：它和另外两页一样自己领滚动，但以前没人
// 钉着它，改回「什么都滚不了」也不会红。
const PAGE_SCROLLERS = ['FeedbackCenterPage', 'FeedbackDetailPage', 'FeedbackMinePage']

describe('反馈页面的滚动归自己领', () => {
  for (const page of PAGE_SCROLLERS) {
    it(`${page} 的根节点自己滚`, () => {
      const tag = rootTag(page)
      expect(tag.startsWith('<div')).toBe(true)
      expect(tag).toContain('fill-height')
      expect(tag).toContain('overflow-y-auto')
    })
  }

  it('管理端不再让整页一起滚，但满高这件事换了个地方继续成立', () => {
    // 根节点不再带 `overflow-y-auto`，所以上面那条不适用 —— 但「满高」这件事不能丢：
    // 根节点塌成内容高，下面那个 `1 1 auto` 的格子就没有可分配的高度，格子内部也就
    // 没有溢出可滚，整页又会回到「被外壳裁掉且没人能滚」。
    //
    // **领这一条的元素换过名字，所以这个用例也换过靶子**：管理端重做之后
    // `/admin/feedback` 变成一层薄壳（`AdminFeedbackPage.vue` 现在只渲染
    // `<AdminQueuePage />`），满高和滚动都搬到了 `views/admin/AdminQueuePage.vue`
    // 的 `.qpage`。钉旧名字的代价是这条不变量会在没人发现的情况下失效 —— 薄壳本身
    // 没有样式，它「有 `fbadmin` 类」这件事跟「页面能不能滚」已经没有关系了。
    const rule = cssRule('views/admin/AdminQueuePage.vue', '.qpage')
    expect(rule).toContain('height: 100%')
    expect(rule).toContain('min-height: 0')
    // 而且真的有一个能滚的格子在里面，不是「把滚动挪走了」就完事。
    expect(cssRule('components/admin/AdminGrid.vue', '.agrid__scroll')).toContain('overflow: auto')
  })

  it('薄壳那条老地址仍然指向队列', () => {
    // 这一条钉的是**链接还活着**，不是布局：老书签、老通知、别人贴在聊天里的链接都还
    // 指着 `/admin/feedback`，它当时指的是「后台里管反馈的那一块」，今天还是那件事。
    // （薄壳的来龙去脉和它为什么不用重定向见 `AdminFeedbackPage.vue` 顶部那段。）
    expect(rootTag('AdminFeedbackPage')).toContain('<AdminQueuePage')
  })
})
