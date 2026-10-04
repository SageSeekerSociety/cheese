/**
 * 这一格管的是「正文怎么读」和「选中的是哪一段」。正文那半边的判据（渲染成文档的样子、
 * 剥掉文件里带进来的脚本）在面板那几条用例里（PanelPreview.media.spec.ts），这里只盯
 * 手势：mouseup 拿到的选区要变成一句话出去，没选中的点击不该出去。
 */
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, expect, it } from 'vitest'

import PreviewMarkdown from './PreviewMarkdown.vue'

afterEach(() => {
  cleanup()
  window.getSelection()?.removeAllRanges()
})

it('选中的一段原文，连同它所在的那一节报出去', async () => {
  const ui = render(PreviewMarkdown, { props: { source: '# 配置\n\n失败以后重试 3 次。\n' } })
  const md = ui.getByTestId('markdown')
  // 正文的读法第一次用到才加载，画出来要等一下。
  const node = await waitFor(() => md.querySelector('p')!.firstChild!)
  const range = document.createRange()
  range.setStart(node, 4)
  range.setEnd(node, 10)
  const selection = window.getSelection()!
  selection.removeAllRanges()
  selection.addRange(range)
  await fireEvent.mouseUp(md)

  const [quote] = ui.emitted().quote![0] as [{ text: string; heading: string; prefix: string }]
  expect(quote.text).toBe('重试 3 次')
  expect(quote.heading).toBe('配置')
  expect(quote.prefix.endsWith('失败以后')).toBe(true)
})

it('没选中东西的点击不发事件', async () => {
  const ui = render(PreviewMarkdown, { props: { source: '# 配置\n\n一段话。\n' } })
  window.getSelection()?.removeAllRanges()
  await fireEvent.mouseUp(ui.getByTestId('markdown'))
  expect(ui.emitted().quote).toBeUndefined()
})

it('还没有正文时什么都不画', () => {
  const ui = render(PreviewMarkdown, { props: { source: null } })
  expect(ui.getByTestId('markdown').innerHTML).toBe('')
})
