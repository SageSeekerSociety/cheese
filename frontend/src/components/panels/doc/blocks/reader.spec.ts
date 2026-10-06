// What a reader sees of Markdown shown outside the editor: a message, a file,
// an old version. The rules are the reader's, stated without the code: it
// reads blocks the way a document does, it never runs what the text carries,
// and a reference shows whom or what it names.
import { afterEach, describe, expect, it } from 'vitest'

import { plainText, readMarkdown } from '../../../../lib/docRead'

import { mountMarkdown } from './reader'

const hosts: HTMLElement[] = []
function show(md: string, opts: Parameters<typeof mountMarkdown>[2] = {}): HTMLElement {
  const host = document.createElement('div')
  document.body.append(host)
  hosts.push(host)
  mountMarkdown(host, md, opts)
  return host
}
afterEach(() => {
  for (const host of hosts.splice(0)) host.remove()
})

const CHART = `:::chart bar
| 周     | 次数  |
| ----- | --- |
| 第 1 周 | 120 |
:::`

describe('reading Markdown outside the editor', () => {
  it('reads a block in a message exactly as a document reads it', () => {
    const text = `结论 {✓ 通过}\n\n${CHART}\n\n> [!WARNING]\n> 周三前要回复`
    expect(readMarkdown(text, 'chat').toJSON()).toEqual(readMarkdown(text, 'doc').toJSON())
  })

  it('breaks the line where a message has a single newline, and nowhere in a document', () => {
    expect(show('第一行\n第二行', { as: 'chat' }).querySelector('br')).not.toBeNull()
    expect(show('第一行\n第二行').querySelector('br')).toBeNull()
  })

  it('shows a chart, a status tag and a callout as blocks, not as their spelling', () => {
    const host = show(`结论 {✓ 通过}\n\n${CHART}\n\n> [!WARNING]\n> 周三前要回复`, { as: 'chat' })
    expect(host.textContent).not.toContain(':::')
    expect(host.textContent).not.toContain('{✓')
    expect(host.textContent).not.toContain('[!WARNING]')
    expect(host.querySelector('[data-block="chart"]')).not.toBeNull()
    expect(host.querySelector('[data-status="ok"]')?.textContent).toBe('通过')
    expect(host.querySelector('[data-block="callout"]')?.textContent).toContain('周三前要回复')
  })

  it('never runs what the text carries', () => {
    const host = show(
      [
        '<script>window.__ran = 1</script>',
        '<img src="x" onerror="window.__ran = 1">',
        '[点这里](javascript:alert(1))',
        '<a href="javascript:alert(1)">也点这里</a>',
      ].join('\n\n'),
      { as: 'chat' }
    )
    expect(host.querySelector('script')).toBeNull()
    for (const el of Array.from(host.querySelectorAll('*'))) {
      for (const attr of Array.from(el.attributes)) {
        expect(attr.name.startsWith('on')).toBe(false)
        expect(attr.value.toLowerCase()).not.toContain('javascript:')
      }
    }
  })

  it('shows a reference as the name it points to, but leaves code as written', () => {
    const host = show('交给 <@zhangsan> 看 `<@zhangsan>`', {
      as: 'chat',
      names: { mentionNames: { zhangsan: '张三' }, topicTitles: {} },
    })
    const chip = host.querySelector('[data-handle="zhangsan"]')
    expect(chip?.textContent).toBe('@张三')
    expect(host.querySelector('code')?.textContent).toBe('<@zhangsan>')
  })

  it('gives a code block a wrap toggle beside the copy button when asked', () => {
    const host = show('```\nconst x = 1\n```', { as: 'chat', copyCode: true })
    const pre = host.querySelector('pre')
    expect(pre).not.toBeNull()
    const wrap = host.querySelector<HTMLButtonElement>('.md-wrap-btn')
    expect(wrap).not.toBeNull()
    expect(host.querySelector('.md-code-btn')).not.toBeNull()

    wrap?.click()
    expect(pre?.classList.contains('md-wrap')).toBe(true)
    expect(wrap?.getAttribute('aria-pressed')).toBe('true')

    wrap?.click()
    expect(pre?.classList.contains('md-wrap')).toBe(false)
    expect(wrap?.getAttribute('aria-pressed')).toBe('false')
  })

  it('leaves a code block bare when its controls were not asked for', () => {
    const host = show('```\nconst x = 1\n```', { as: 'chat' })
    expect(host.querySelector('pre')).not.toBeNull()
    expect(host.querySelector('.md-pre-bar')).toBeNull()
  })

  it('reads as one line of words for a quote', () => {
    const line = plainText(`**结论**：{✓ 通过} {✗ 超时}\n\n${CHART}`, 'chat')
    expect(line).not.toMatch(/[*:{}|]/)
    expect(line).toContain('✓ 通过')
    expect(line).toContain('✗ 超时')
  })
})

describe('a table of figures', () => {
  it('lines a column of figures up on the right, and leaves a column of words alone', () => {
    const host = show(
      '| 周 | 改版前 | 结论 |\n| --- | --- | --- |\n| 第 1 周 | 1,020 | 保留 |\n| 第 2 周 | 1,060 | 改回 |'
    )
    const column = (i: number) =>
      Array.from(host.querySelectorAll('tr')).map((row) => (row.children[i] as HTMLElement).dataset.num !== undefined)
    expect(column(1)).toEqual([true, true, true])
    expect(column(0)).toEqual([false, false, false])
    expect(column(2)).toEqual([false, false, false])
  })
})
