/**
 * 批注卡开着的时候，底色还在不在。
 *
 * 点开「添加批注」会把光标交给输入框，浏览器随即把页面上的选区收起来。底色是照选区画的，
 * 只认 `window.getSelection()` 的话，那一下底色就灭了——读者正在写的那一段突然不亮，
 * 看不出自己在说哪儿。所以这一层还要认一份「留下的副本」（`kept`）。
 *
 * 把 `kept` 那一支去掉，这条会红（底色在「点开卡」那一步变成空串）。
 */
import { defineComponent, h, nextTick } from 'vue'
import { cleanup, render } from '@testing-library/vue'
import { afterEach, beforeAll, describe, expect, it } from 'vitest'

import { useSelectionHighlight } from './selectionHighlight'

/** 这一页上那一段文字的框：happy-dom 没有排版，所有 Range 都按这个固定框量。 */
const RECT = { left: 100, top: 50, width: 100, height: 12, right: 200, bottom: 62 } as DOMRect

beforeAll(() => {
  Range.prototype.getClientRects = function () {
    return [RECT] as unknown as DOMRectList
  }
  Range.prototype.getBoundingClientRect = function () {
    return RECT
  }
  // happy-dom 没有排版，所有元素的框都是 0；底色那层是组件自己建的，拿到的框也是 0，
  // 于是「这一块落在纸外」的判据把每一块都丢掉了。给每个元素一个纸那么大的框。
  Element.prototype.getBoundingClientRect = function () {
    return { left: 0, top: 0, width: 600, height: 800, right: 600, bottom: 800 } as DOMRect
  }
})
afterEach(cleanup)

function makeSheet() {
  const sheet = document.createElement('div')
  sheet.className = 'sheet'
  const layer = document.createElement('div')
  layer.className = 'pv-text'
  // 文字直接放在层里，不套 span：真产品里它套在 pdf.js 造的 span 里，但 happy-dom 的
  // TreeWalker 不下钻（`walked ["a","d"]` 这种，里层的文字根本走不到），套着就一段也
  // 收不到。这一条测的是「副本还在不在」，跟套不套 span 无关。
  layer.textContent = '请把报名表发到邮箱'
  sheet.append(layer)
  document.body.append(sheet)
  sheet.getBoundingClientRect = () => ({ left: 0, top: 0, width: 600, height: 800, right: 600, bottom: 800 }) as DOMRect
  const node = layer.firstChild!
  const range = document.createRange()
  range.selectNodeContents(node)
  return { sheet, node, range }
}

describe('留下的那份选区', () => {
  it('浏览器收了选区但副本还在时，底色照旧留着；副本清了才灭', async () => {
    const { sheet, range } = makeSheet()
    let kept: Range | null = null

    const Comp = defineComponent({
      setup() {
        useSelectionHighlight({
          sheets: () => [sheet],
          layerClass: 'pv-text',
          paintClass: 'pv-hl',
          kept: () => kept,
        })
        return () => h('div')
      },
    })
    const ui = render(Comp)
    const d = () => (document.querySelector('.pv-hl path') as SVGPathElement).getAttribute('d')

    // 读者选中一段：浏览器还留着选区，底色按它画。
    const selection = window.getSelection()!
    selection.removeAllRanges()
    selection.addRange(range)
    document.dispatchEvent(new Event('selectionchange'))
    await nextTick()
    expect(d()).toBeTruthy()
    const whileLive = d()

    // 点开「添加批注」：光标进输入框，浏览器把页面上的选区收掉；副本还在。
    kept = range
    selection.removeAllRanges()
    document.dispatchEvent(new Event('selectionchange'))
    await nextTick()
    expect(d()).toBe(whileLive)

    // 卡关掉：副本也清了，底色跟着灭。
    kept = null
    document.dispatchEvent(new Event('selectionchange'))
    await nextTick()
    expect(d()).toBe('')
    ui.unmount()
  })
})
