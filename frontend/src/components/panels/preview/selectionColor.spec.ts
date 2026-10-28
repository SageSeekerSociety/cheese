/// <reference types="node" />
/**
 * 选中底色的色号，两档分页阅读器必须是同一个。
 *
 * 分页文档（`PreviewPages`）和幻灯片（`PreviewSlides`）各自画自己的选区底色，两边都写着
 * 「和那一处同一套写法，改一处时两处一起改」——但这两句约定没有东西在检查，于是就漂了：
 * 一处填 `--selection-bg`、另一处填 `--accent-wash`，同一个「选中一段」的动作在两档里是
 * 两个颜色。哪一个是"对"的不好说，能说的是它们必须一样。
 *
 * jsdom 算不出 SVG 的 `fill` 继承，照仓库的老办法（`offScreenSkipping.spec.ts`、
 * `vShowDisplayUtilities.spec.ts`）读源码断言。只钉这一个性质：**两处的变量名相同**。
 * 至于那个变量自己是什么颜色，是 `style.css` 的事，不在这里管。
 */
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

const SRC = dirname(fileURLToPath(import.meta.url))

function read(name: string): string {
  return readFileSync(join(SRC, name), 'utf8')
}

/** 那一层里 `fill:` 写的变量名。选择器两处写法不同（`:deep(.pv-hl path)` / 
 *  `:deep(.slide-hl path)`），所以按块找：切出每一段声明，取提到这一层又填了色的那一段。 */
function fillVariable(source: string, layerClass: string): string | null {
  for (const block of source.split('}')) {
    if (!block.includes(`${layerClass} path`) && !block.includes(`${layerClass}) path`)) continue
    const variable = /fill:\s*var\((--[a-z-]+)\)/.exec(block)
    if (variable) return variable[1]
  }
  return null
}

describe('选中底色', () => {
  it('分页文档和幻灯片用的是同一个变量', () => {
    const pages = fillVariable(read('PreviewPages.vue'), 'pv-hl')
    const slides = fillVariable(read('PreviewSlides.vue'), 'slide-hl')
    expect(pages).not.toBeNull()
    expect(slides).not.toBeNull()
    expect(slides).toBe(pages)
  })
})
