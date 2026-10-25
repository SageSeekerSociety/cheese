/**
 * 这一格管的是「正文怎么读」「选中的是哪一段」和「已有的评论画在哪几处」。正文那半边的
 * 判据（渲染成文档的样子、剥掉文件里带进来的脚本）在面板那几条用例里
 * （PanelPreview.media.spec.ts）；这里盯手势：mouseup 拿到的选区要变成一句话出去、没选中的
 * 点击不该出去、点标记要开那一串；以及标记本身：哪几处画、画重的在哪、什么时候拆掉。
 *
 * 正文的读法是第一次用到才加载的，所以这个文件里凡是等它画出来的 `waitFor` 都给足 5 秒：
 * 默认的 1 秒单独跑够，整目录 80 来个文件并发跑时不够。
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
  const node = await waitFor(() => md.querySelector('p')!.firstChild!, { timeout: 5000 })
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
  // 正文的读法是懒加载的：整目录并发跑时那一次 import 可能超过默认的 1 秒。
  return waitFor(
    () => {
      const found = md.querySelector<HTMLElement>(`mark[data-file-comment="${id}"]`)
      expect(found).toBeTruthy()
      return found!
    },
    { timeout: 5000 }
  )
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

it('一句话里夹着加粗，标出来的两段都点得动', async () => {
  // 跨节点的标记是好几颗，每一颗都接了点击才都点得动。只给其中一颗接，其余几段是
  // 看得见、点不开的标记——读者点在「重试」上什么都没发生。
  const ui = render(PreviewMarkdown, {
    props: {
      source: '# 配置\n\n失败以后**重试 3 次**。\n',
      anchors: [{ id: 'c1', quote: { text: '以后重试 3 次', heading: '配置', prefix: '失败', suffix: '。' } }],
    },
  })
  const md = ui.getByTestId('markdown')
  await waitFor(() => expect(md.querySelectorAll('mark[data-file-comment="c1"]')).toHaveLength(2), { timeout: 5000 })

  await fireEvent.click(md.querySelector('mark[data-file-comment="c1"]')!)
  expect(ui.emitted().anchor).toEqual([['c1']])
  await fireEvent.click(md.querySelectorAll('mark[data-file-comment="c1"]')[1]!)
  expect(ui.emitted().anchor).toEqual([['c1'], ['c1']])
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

it('换了在看的那一条，正文上重的那一处跟着换', async () => {
  // 正文和指的那几处都没变——连那个数组都还是同一个（不然重画是「指的那几处变了」那条
  // 路带来的，测不到「在看哪一条」那一条路）。这条路上要是没重画，读者在栏里换了另一条
  // 看，正文还亮着上一条。
  const anchors = [{ id: 'c1', quote: ANCHOR }]
  const ui = render(PreviewMarkdown, {
    props: { source: '# 配置\n\n失败以后重试 3 次。\n', anchors, activeId: null },
  })
  const md = ui.getByTestId('markdown')
  expect((await mark(md, 'c1')).classList.contains('is-active')).toBe(false)

  await ui.rerender({ source: '# 配置\n\n失败以后重试 3 次。\n', anchors, activeId: 'c1' })

  await waitFor(
    () => expect(md.querySelector('mark[data-file-comment="c1"]')?.classList.contains('is-active')).toBe(true),
    { timeout: 5000 }
  )
  // 重画是「先拆再画」，不是又包一层：正文上还只有这一颗。
  expect(md.querySelectorAll('mark[data-file-comment]')).toHaveLength(1)
})

it('正文里已经找不到的那一处不画：那一段话被删了或改了', async () => {
  // 评论记的是「哪一版 + 那段原文」。换了一版那一段话就没了，照旧位置画会指到另一句话上，
  // 比不画更坏。
  //
  // 同一次里还挂着一条找得回来的：等它画出来，才说明这一轮画过了。只等正文的 <p> 出现
  // 就断言「没有标记」，一个根本还没画的时刻也满足它——实测过，把「找不到就别画」那段
  // 代码改坏，这条照样绿。
  const ui = render(PreviewMarkdown, {
    props: {
      source: '# 配置\n\n失败以后重试 3 次。\n',
      anchors: [
        { id: 'c1', quote: { text: '这一段话已经不在这一版里了', heading: '配置', prefix: '', suffix: '' } },
        { id: 'c2', quote: ANCHOR },
      ],
    },
  })
  await mark(ui.getByTestId('markdown'), 'c2')
  expect(ui.getByTestId('markdown').querySelector('mark[data-file-comment="c1"]')).toBeNull()
})

it('换了一份正文，上一份的标记不留在新的正文上', async () => {
  const ui = render(PreviewMarkdown, {
    props: { source: '# 配置\n\n失败以后重试 3 次。\n', anchors: [{ id: 'c1', quote: ANCHOR }] },
  })
  await mark(ui.getByTestId('markdown'), 'c1')

  // 新的这一份里也留一条指得上的：不然「新正文上有没有旧标记」这件事在一个还没画过的
  // 时刻也成立。换正文这一路上标记是跟着正文一起没的（阅读视图整块重画），所以这条主要
  // 拦的是「新正文上顺手把旧标记又画了一遍」；「画之前先拆掉上一轮那一层」那件事由
  // 「换了在看的那一条」那条盯着——那一次正文没变，只有标记本身要重画。
  const next = { text: '另一句话', heading: '配置', prefix: '', suffix: '。' }
  await ui.rerender({ source: '# 配置\n\n另一句话。\n', anchors: [{ id: 'c2', quote: next }] })

  const md = ui.getByTestId('markdown')
  await mark(md, 'c2')
  expect(md.querySelector('mark[data-file-comment="c1"]')).toBeNull()
  expect(md.textContent).not.toContain('重试 3 次')
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

it('两条评论指着重叠的文字：点里面那颗开的是里面这一条', async () => {
  // 一段话里的半句和整句都被评过时，两颗标记是套着的（外面整句、里面半句）。点里面那颗，
  // 事件会一路冒到外面那颗上——不挡住的话最后打开的是外面那一串，点谁开谁就成了空话。
  const ui = render(PreviewMarkdown, {
    props: {
      source: '# 配置\n\n失败以后重试 3 次。\n',
      anchors: [
        { id: 'whole', quote: { text: '失败以后重试 3 次', heading: '配置', prefix: '', suffix: '。' } },
        { id: 'half', quote: { text: '重试 3 次', heading: '配置', prefix: '失败以后', suffix: '。' } },
      ],
    },
  })
  const md = ui.getByTestId('markdown')
  await fireEvent.click(await mark(md, 'half'))

  expect(ui.emitted().anchor).toEqual([['half']])
})

it('原文里是中文全角空格，也照样找得回来', async () => {
  // 取评论那一侧把空白归一成半角空格（`/\s+/`），找的这边要是只认半角空格和换行，
  // 全角空格 U+3000 就原样留着——存下的那句话和这里的正文永远对不上，那一处再也画不出来。
  // 中文文档里全角空格是普通正文，不是角上的情况。
  const ui = render(PreviewMarkdown, {
    props: {
      source: '# 配置\n\n失败\u3000以后重试 3 次。\n',
      anchors: [{ id: 'c1', quote: { text: '失败 以后重试 3 次', heading: '配置', prefix: '', suffix: '。' } }],
    },
  })
  const drawn = await mark(ui.getByTestId('markdown'), 'c1')
  // 标记盖住的是原文那几个字，全角空格照原样留着（画标记不改正文）。
  expect(drawn.textContent).toBe('失败\u3000以后重试 3 次')
})
