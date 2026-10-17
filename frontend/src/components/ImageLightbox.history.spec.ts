// @vitest-environment jsdom
// happy-dom 的 history 什么也不做；这几条要的是真的会退、会发 popstate 的历史，所以用 jsdom。
//
// 手机上看一张放大的图，返回手势/返回键是「不看了」，不是「离开这一页」。关掉之后，
// 历史里也不能留一格多出来的记录：不然下一次返回原地不动，人以为按坏了。
//
// jsdom 的 back() 不当场生效：它把这次遍历排进队里，过一会儿才改 state、发 popstate。
// 所以每条都等那一次 popstate，不按毫秒数估——等多久是这台机器忙不忙说了算。
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, screen } from '@testing-library/vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import ImageLightbox from './ImageLightbox.vue'

import { setLocale } from '@/i18n'

/** 房间页那一格历史记录：打开预览之前人站着的地方。 */
const PAGE = { page: 'room' }

beforeEach(() => {
  setLocale('zh-CN')
  history.replaceState(PAGE, '')
})
afterEach(() => {
  vi.restoreAllMocks()
  cleanup()
})

function mount() {
  const onClose = vi.fn()
  const utils = render(ImageLightbox, {
    props: { src: 'blob:image-1', alt: '图.png', onClose },
    global: { plugins: [createVuetify({ components, directives })] },
  })
  return { onClose, ...utils }
}

/** 等到历史真的退完：popstate 发出来时 state 已经换好了。 */
function nextPopState() {
  return new Promise<void>((resolve) => {
    window.addEventListener('popstate', () => resolve(), { once: true })
  })
}

it('closes on Back and keeps the reader on the page', async () => {
  const { onClose } = mount()
  const href = location.href

  const popped = nextPopState()
  history.back()
  await popped

  expect(onClose).toHaveBeenCalledTimes(1)
  expect(location.href).toBe(href)
  expect(history.state).toEqual(PAGE)
})

it('leaves no extra history entry behind when closed some other way', async () => {
  const { onClose, unmount } = mount()

  await fireEvent.click(screen.getByRole('button', { name: '关闭' }))
  expect(onClose).toHaveBeenCalledTimes(1)
  // 父组件收到 close 就把这一层拿掉；拿掉的时候它自己把压进去的那一格退掉，
  // 退完会发 popstate。
  const popped = nextPopState()
  unmount()
  await popped

  // 回到打开之前那一格：下一次返回就是离开这一页，不会原地空按一下。
  expect(history.state).toEqual(PAGE)
})

it('does not step back a second time after Back already closed it', async () => {
  // 人是从列表页进的房间页：身后还有一格。
  history.replaceState({ page: 'list' }, '')
  history.pushState(PAGE, '')
  const { onClose, unmount } = mount()

  const popped = nextPopState()
  history.back()
  await popped

  // 只退了自己压的那一格：人还在房间页，没被多退回列表页。
  expect(onClose).toHaveBeenCalledTimes(1)
  expect(history.state).toEqual(PAGE)

  // 这一次返回已经替它退过了。再退一次就把读者退回列表页，所以卸载时不许再请求。
  // 「不会再发生的事」等不来，直接看它有没有再调一次返回。
  const back = vi.spyOn(window.history, 'back')
  unmount()
  expect(back).not.toHaveBeenCalled()
})
