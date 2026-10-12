// @vitest-environment jsdom
// 文档里引用的图：画出来的是取回来的那一份，存回去的还是原来的路径；取不到的不留一个
// 会再去匿名请求的地址。
import { Editor } from '@tiptap/core'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { docExtensions } from './index'

const editors: Editor[] = []
afterEach(() => {
  editors.splice(0).forEach((e) => e.destroy())
  vi.restoreAllMocks()
})

function editor(markdown: string, loadImage: (src: string) => Promise<string>) {
  const element = document.createElement('div')
  document.body.appendChild(element)
  const ed = new Editor({ element, extensions: docExtensions({ loadImage }) })
  ed.commands.setContent(markdown, { contentType: 'markdown' })
  editors.push(ed)
  return ed
}

const settle = () => new Promise((resolve) => setTimeout(resolve, 0))

describe('文档里的图', () => {
  it('画的是取回来的字节，存回去的是原来的路径', async () => {
    const load = vi.fn().mockResolvedValue('blob:figure')
    const ed = editor('![耗时对比](figures/耗时.png)\n', load)
    await settle()

    expect(load).toHaveBeenCalledWith('figures/耗时.png')
    const img = ed.view.dom.querySelector('img')!
    expect(img.getAttribute('src')).toBe('blob:figure')
    expect(img.getAttribute('alt')).toBe('耗时对比')
    expect(ed.getMarkdown()).toContain('![耗时对比](figures/耗时.png)')
  })

  it('取不到时只留下说明文字，不留路径', async () => {
    const ed = editor('![架构](library/架构.png)\n', () => Promise.reject(new Error('404')))
    await settle()

    const img = ed.view.dom.querySelector('img')!
    expect(img.hasAttribute('src')).toBe(false)
    expect(img.getAttribute('alt')).toBe('架构')
  })

  it('图从文档里拿掉后，取回来的那一份就放掉', async () => {
    const revoke = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {})
    const ed = editor('![图](a.png)\n', async () => 'blob:a')
    await settle()

    ed.commands.setContent('没有图了\n', { contentType: 'markdown' })
    expect(revoke).toHaveBeenCalledWith('blob:a')
  })
})
