/** 四个反馈页面必须自己领滚动。
 *
 * 全站约定：`styles/common.scss` 把 `html / body / #app` 定成固定高度加
 * `overflow: hidden`，**滚动由每一页自己领** —— 别的页面都写着
 * `<div class="overview-page fill-height overflow-y-auto">` 这一组（OverviewView、
 * MemberView、MarketView、ProjectDocsView…十来个）。
 *
 * 反馈这四页当初只写了页面自己的类。于是内容比窗口高的时候，下半截被外壳那层
 * `overflow-hidden` 裁掉，而页面上**没有任何元素能滚**：1280×600 的窗口里反馈中心
 * 有 117px 永远够不着（窗口高到 720 正好放得下，所以一直没露出来）。
 *
 * 这是 CSS，jsdom 量不到布局，照仓库的老办法用源码断言钉住（见
 * `components/ChatPanel.mentionMenu.spec.ts`）。注释里也会出现这两个类名，先摘掉
 * 再断言，免得我自己写的注释把测试骗绿。
 */
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

const dir = dirname(fileURLToPath(import.meta.url))

/** `<template>` 里第一个元素 —— 也就是这个页面的根节点。多元素会直接报出来，
 *  因为 `buildRoot` 只取第一行，那种模板本来也不是这一页该有的样子。 */
function rootTag(page: string): string {
  const src = readFileSync(join(dir, `${page}.vue`), 'utf8')
  const open = src.indexOf('<template>')
  const close = src.indexOf('</template>', open)
  const body = src.slice(open + '<template>'.length, close).replace(/<!--[\s\S]*?-->/g, '')
  return body.trim().split('\n')[0]
}

const PAGES = ['FeedbackCenterPage', 'FeedbackDetailPage', 'AdminFeedbackPage', 'FeedbackDesignPage']

describe('反馈页面的滚动归自己领', () => {
  for (const page of PAGES) {
    it(`${page} 的根节点自己滚`, () => {
      const tag = rootTag(page)
      expect(tag.startsWith('<div')).toBe(true)
      expect(tag).toContain('fill-height')
      expect(tag).toContain('overflow-y-auto')
    })
  }
})
