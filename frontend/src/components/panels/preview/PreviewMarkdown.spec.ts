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

// ---- 把评论画在正文上 ----
//
// 位置是拿评论存下的那句话回正文里找的（`markdownAnchors`），这里盯的是这一格接得对不对：
// 找回来的包成标记、点它把那一串评论叫出来、找不到的不画。

const ANCHOR = { text: '重试 3 次', heading: '配置', prefix: '失败以后', suffix: '。' }

/** 正文上那一颗标记，画出来要等正文自己加载完。 */
function mark(md: HTMLElement, id: string): Promise<HTMLElement> {
  return waitFor(() => {
    const found = md.querySelector<HTMLElement>(`mark[data-file-comment="${id}"]`)
    expect(found).toBeTruthy()
    return found!
  })
}

it('评论记下的那一处，在今天的正文里找回来包成标记', async () => {
  const ui = render(PreviewMarkdown, {
    props: { source: '# 配置\n\n失败以后重试 3 次。\n', anchors: [{ id: 'c1', quote: ANCHOR }] },
  })
  const found = await mark(ui.getByTestId('markdown'), 'c1')
  expect(found.textContent).toBe('重试 3 次')
  // 正文本身没被改动：标记是包在外面的一层，不是替换掉那几个字。
  expect(ui.getByTestId('markdown').textContent).toContain('失败以后重试 3 次。')
})

it('点正文上的标记，把那一串评论叫出来', async () => {
  const ui = render(PreviewMarkdown, {
    props: { source: '# 配置\n\n失败以后重试 3 次。\n', anchors: [{ id: 'c1', quote: ANCHOR }] },
  })
  await fireEvent.click(await mark(ui.getByTestId('markdown'), 'c1'))
  expect(ui.emitted().anchor).toEqual([['c1']])
})

it('正看着的那一串画得重一些', async () => {
  const ui = render(PreviewMarkdown, {
    props: {
      source: '# 配置\n\n失败以后重试 3 次。\n',
      anchors: [
        { id: 'c1', quote: ANCHOR },
        { id: 'c2', quote: { text: '失败以后', heading: '配置', prefix: '', suffix: '重试' } },
      ],
      activeId: 'c2',
    },
  })
  const md = ui.getByTestId('markdown')
  expect((await mark(md, 'c2')).classList.contains('is-active')).toBe(true)
  expect((await mark(md, 'c1')).classList.contains('is-active')).toBe(false)
})

it('正文里已经找不到的那一处不画：那一段话被删了或改了', async () => {
  // 评论记的是「哪一版 + 那段原文」。换了一版那一段话就没了，照旧位置画会指到另一句话上，
  // 比不画更坏。
  const ui = render(PreviewMarkdown, {
    props: {
      source: '# 配置\n\n失败以后重试 3 次。\n',
      anchors: [{ id: 'c1', quote: { text: '这一段话已经不在这一版里了', heading: '配置', prefix: '', suffix: '' } }],
    },
  })
  await waitFor(() => expect(ui.getByTestId('markdown').querySelector('p')).toBeTruthy())
  expect(ui.getByTestId('markdown').querySelector('mark[data-file-comment]')).toBeNull()
})

it('换了一份正文，上一份的标记不留在新的正文上', async () => {
  const ui = render(PreviewMarkdown, {
    props: { source: '# 配置\n\n失败以后重试 3 次。\n', anchors: [{ id: 'c1', quote: ANCHOR }] },
  })
  await mark(ui.getByTestId('markdown'), 'c1')

  await ui.rerender({
    source: '# 配置\n\n另一句话。\n',
    anchors: [{ id: 'c1', quote: ANCHOR }],
  })

  const md = ui.getByTestId('markdown')
  await waitFor(() => expect(md.textContent).toContain('另一句话'))
  expect(md.querySelector('mark[data-file-comment]')).toBeNull()
})

it('从标记里拖选了一段：那是「指着这处」，不是「打开这串」', async () => {
  // 拖选的起点和落点都可能在这颗标记里，浏览器照样会来一次 `click`。这时读者要的是
  // 选中的那一段（`mouseup` 那条路已经把它发出去了），不能再把评论栏打开——两个动作
  // 同时发生，读者会以为自己的选区把一栏评论顶了出来。
  const ui = render(PreviewMarkdown, {
    props: { source: '# 配置\n\n失败以后重试 3 次。\n', anchors: [{ id: 'c1', quote: ANCHOR }] },
  })
  const found = await mark(ui.getByTestId('markdown'), 'c1')
  const node = found.firstChild as Text
  const range = document.createRange()
  range.setStart(node, 0)
  range.setEnd(node, 4)
  const selection = window.getSelection()!
  selection.removeAllRanges()
  selection.addRange(range)

  await fireEvent.click(found)

  expect(ui.emitted().anchor).toBeUndefined()
})
