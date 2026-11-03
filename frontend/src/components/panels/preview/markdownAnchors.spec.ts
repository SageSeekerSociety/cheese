/**
 * 把评论记下的那一处，在今天的正文里找回来。
 *
 * 这是「在文件上把评论画成标记」的全部判据：找到了才画，找不到就不画。
 * 两条必须成立的事：
 *
 * - 一段话在正文里出现好几次时，靠标题和前后文认出是哪一次；认不出来就不画——
 *   把标记钉到另一句话上比没有标记更坏。
 * - 渲染出来的空白（换行、缩进）和当初选中时的原文对不上，所以按归一后的样子比。
 */
import { describe, expect, it } from 'vitest'

import { clearMarks, clearPins, locateQuote, MARK_ATTR, PIN_ATTR, wrapRange } from './markdownAnchors'

/** 一段渲染出来的正文。中间那个换行是 HTML 里的排版空白，原文里没有。 */
function doc(html: string): HTMLElement {
  const root = document.createElement('div')
  root.innerHTML = html
  return root
}

const BODY = doc(`
  <h2>配置</h2>
  <p>失败以后重试
     3 次。</p>
  <h2>部署</h2>
  <p>失败以后重试 3 次。</p>
`)

describe('在正文里找回评论指着的那一处', () => {
  it('原文在正文里、但渲染出来的空白和当初不一样，照样找得回来', () => {
    // 当初选中时取的是「失败以后重试 3 次」，而 HTML 里那个换行是排版用的。
    const range = locateQuote(BODY, {
      text: '失败以后重试 3 次',
      heading: '配置',
      prefix: '',
      suffix: '',
    })
    expect(range).not.toBeNull()
    // 圈住的正是那几个字（里面那个换行是原文自己的，标出来时也连着它）。
    expect(range!.toString().replace(/\s+/g, ' ')).toBe('失败以后重试 3 次')
    // 「配置」那一节在前，所以找到的是头一处。
    expect(range!.startContainer.parentElement?.closest('p')?.previousElementSibling?.textContent).toBe('配置')
  })

  it('同一句话出现两次时，按标题认出是哪一次', () => {
    const range = locateQuote(BODY, {
      text: '失败以后重试 3 次',
      heading: '部署',
      prefix: '',
      suffix: '',
    })
    expect(range).not.toBeNull()
    expect(range!.startContainer.parentElement?.closest('p')?.previousElementSibling?.textContent).toBe('部署')
  })

  it('两处一样像的时候宁可不画，哪怕它们都「很像」', () => {
    // 这一条要的是**分数不为零**的平手：同一个标题下两次一样的出现，前后文也一样，两边
    // 都拿满分。分不清就别画——把标记钉到另一句话上比没有标记更坏。
    const twice = doc('<h1>配置</h1><p>甲说重试 3 次。</p><p>甲说重试 3 次。</p>')
    expect(locateQuote(twice, { text: '重试 3 次', heading: '配置', prefix: '甲说', suffix: '。' })).toBeNull()
  })

  it('两处都没得可分辨（一分不给）的时候也不画', () => {
    // 这一条走的是另一条判据：两处都是 0 分，横竖都认不出是哪一处。和上面那条是两道
    // 各自会坏的坎，所以要各有一条——只留一条的话，另一道坎被拆掉时没人拦。
    const twice = doc('<p>重试 3 次。</p><p>重试 3 次。</p>')
    expect(locateQuote(twice, { text: '重试 3 次', heading: null, prefix: '', suffix: '' })).toBeNull()
  })

  it('正文里已经没有这句话了，就不画', () => {
    expect(locateQuote(BODY, { text: '这一句不在里面', heading: null, prefix: '', suffix: '' })).toBeNull()
  })

  it('正文还没渲染出来（空节点）也不画', () => {
    expect(locateQuote(null, { text: '重试 3 次', heading: null, prefix: '', suffix: '' })).toBeNull()
  })

  it('前后文能把同名的两处分开时，按前后文认', () => {
    const twice = doc('<p>甲说重试 3 次。</p><p>乙说重试 3 次。</p>')
    const range = locateQuote(twice, { text: '重试 3 次', heading: null, prefix: '乙说', suffix: '' })
    expect(range).not.toBeNull()
    expect(range!.startContainer.parentElement?.textContent).toBe('乙说重试 3 次。')
  })

  it('页面上不该被当成正文的东西（脚本内容）不参与找位置', () => {
    const withScript = doc('<script>重试 3 次</script><p>重试 3 次。</p>')
    const range = locateQuote(withScript, { text: '重试 3 次', heading: null, prefix: '', suffix: '' })
    expect(range).not.toBeNull()
    expect(range!.startContainer.parentElement?.tagName).toBe('P')
  })
})

describe('把标记画到正文上', () => {
  it('画上去的字还是那几个字，正文一个字都没变', () => {
    const root = doc('<p>前 目标 后</p>')
    const before = root.textContent
    const range = locateQuote(root, { text: '目标', heading: null, prefix: '', suffix: '' })!
    const [mark] = wrapRange(range, 't1', false)!
    expect(mark.tagName).toBe('MARK')
    expect(mark.getAttribute(MARK_ATTR)).toBe('t1')
    expect(mark.textContent).toBe('目标')
    expect(root.textContent).toBe(before)
  })

  it('拆掉之后正文恢复原样，还能再按文字找一次', () => {
    const root = doc('<p>前 目标 后</p>')
    const quote = { text: '目标', heading: null, prefix: '', suffix: '' }
    wrapRange(locateQuote(root, quote)!, 't1', false)
    clearMarks(root)
    expect(root.querySelector('mark')).toBeNull()
    expect(root.querySelectorAll('p')[0].childNodes).toHaveLength(1)
    // 拆完再找一次仍然找得到：包了又拆不能把正文切成跨不过去的几段。
    expect(locateQuote(root, quote)).not.toBeNull()
  })

  it('一句话里夹着加粗：跨节点的每一段都标上，而且每一段都交回来', () => {
    // 交回来的那几颗是外面接点击的名单：少一颗，那一段就是看得见、点不开的标记。
    const root = doc('<p>前 目标<strong>加粗</strong>尾</p>')
    const range = locateQuote(root, { text: '目标加粗', heading: null, prefix: '', suffix: '' })!
    expect(range).not.toBeNull()
    const marks = wrapRange(range, 't1', false)!
    expect(marks.map((m) => m.textContent)).toEqual(['目标', '加粗'])
    const marked = Array.from(root.querySelectorAll('mark'))
      .map((m) => m.textContent)
      .join('')
    expect(marked).toBe('目标加粗')
  })

  it('正在看的那一条标记画得显眼些', () => {
    const root = doc('<p>目标</p>')
    const range = locateQuote(root, { text: '目标', heading: null, prefix: '', suffix: '' })!
    const [mark] = wrapRange(range, 't1', true)!
    expect(mark.className).toContain('is-active')
  })
})

describe('攒着的几处挂在正文上的序号', () => {
  it('拆掉序号之后正文一个字不多不少，评论标记也还在', () => {
    const root = doc('<p>前 目标 后</p>')
    const before = root.textContent
    const quote = { text: '目标', heading: null, prefix: '', suffix: '' }
    // 评论标记和序号是两种元素：拆其中一种不能把另一种带下去。
    wrapRange(locateQuote(root, quote)!, 't1', false)
    const range = locateQuote(root, quote)!
    const badge = document.createElement('span')
    badge.className = 'pending-pin'
    badge.setAttribute(PIN_ATTR, 'p1')
    badge.textContent = '2'
    const end = document.createRange()
    end.setStart(range.endContainer, range.endOffset)
    end.collapse(true)
    end.insertNode(badge)
    expect(root.querySelector(`[${PIN_ATTR}]`)?.textContent).toBe('2')

    clearPins(root)
    expect(root.querySelector(`[${PIN_ATTR}]`)).toBeNull()
    expect(root.textContent).toBe(before)
    // 评论标记没被动过，拆序号之后仍然找得到一个 mark。
    expect(root.querySelectorAll('mark')).toHaveLength(1)
    // 拆完还能再按字找一次：序号插在正文里，不许把它切成跨不过去的几段。
    expect(locateQuote(root, quote)).not.toBeNull()
  })
})
