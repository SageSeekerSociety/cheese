// 一台设备那一格：iframe 里按真 px 渲染，外面等比缩。
//
// 要守的是「真 px」这件事本身：框里的宽度是地址上那一档的真实宽度（1280），缩的只是
// 显示。CSS 缩放一个 div 改不了 window 宽度，产品里按窗口切的布局在那种框里不会切。
import { nextTick } from 'vue'
import { render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import CatalogFrame from './CatalogFrame.vue'
import { FRAME_MESSAGE } from './demoCatalogStudio'

const STUB = class {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

beforeEach(() => {
  vi.stubGlobal('ResizeObserver', STUB)
  // 测试里的 iframe 会真的去取 src 上那个地址，取不到就抛一条没人接的错，把测试判红。
  // 这一份要量的是框本身（宽度、缩放、高度），不是去连一台开发服务器。
  window.happyDOM.settings.disableIframePageLoading = true
})

afterEach(() => {
  window.happyDOM.settings.disableIframePageLoading = false
  vi.unstubAllGlobals()
})

function mount(width = 1280): ReturnType<typeof render> {
  return render(CatalogFrame, {
    props: {
      href: '/demo/catalog/user-ref/frame/0?theme=light&motion=normal&bg=surface',
      width,
      label: '桌面 1280',
      title: 'UserRef · 有去处（桌面 1280）',
    },
  })
}

describe('设备框', () => {
  it('框里就是那一页，宽度是这一档的真 px', () => {
    const view = mount()
    const frame = view.container.querySelector('iframe')!
    expect(frame.getAttribute('src')).toBe('/demo/catalog/user-ref/frame/0?theme=light&motion=normal&bg=surface')
    expect(frame.getAttribute('title')).toBe('UserRef · 有去处（桌面 1280）')
    expect(frame.style.width).toBe('1280px')
  })

  it('窄的那一档就是窄的真宽度，不是把宽的那一台缩小', () => {
    const view = mount(390)
    expect(view.container.querySelector('iframe')!.style.width).toBe('390px')
    expect(view.container.querySelector('.frame-width')?.textContent).toBe('390px')
  })

  it('量到内容高度之前先立个占位，框不是空的', () => {
    const view = mount()
    expect(view.container.querySelector('.frame-inner')!.getAttribute('style')).toContain('320px')
  })

  it('摆不下就等比缩，摆得下就说原始大小', () => {
    const view = mount()
    expect(view.container.querySelector('.frame-scale')?.textContent).toBe('原始大小')
  })

  it('报回来的高度不合规矩就不认：还是占位的那个高度', async () => {
    const view = mount()
    const inner = () => view.container.querySelector('.frame-inner')!.getAttribute('style') ?? ''
    // 同一个源里直接读一把是量不到东西的（测试里那一页是空的），所以高度是占位值。
    await nextTick()
    expect(inner()).toContain('320px')

    // 页面上别的东西发的消息：标记对不上，丢掉。
    window.dispatchEvent(new MessageEvent('message', { data: { source: '别的东西', height: 900 } }))
    await nextTick()
    expect(inner()).toContain('320px')

    // 标记对上了，但高度不是个数：也丢掉。
    window.dispatchEvent(new MessageEvent('message', { data: { source: FRAME_MESSAGE, height: 'nine' } }))
    await nextTick()
    expect(inner()).toContain('320px')

    // 量不出高度时那一页刚 load 完：不该把框收成 0。
    view.container.querySelector('iframe')!.dispatchEvent(new Event('load'))
    await nextTick()
    expect(inner()).toContain('320px')
  })
})
