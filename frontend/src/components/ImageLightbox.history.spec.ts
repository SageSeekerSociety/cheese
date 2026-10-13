// @vitest-environment jsdom
// happy-dom 的 history 什么也不做；这几条要的是真的会退、会发 popstate 的历史，所以用 jsdom。
//
// 手机上看一张放大的图，返回手势/返回键是「不看了」，不是「离开这一页」。关掉之后，
// 历史里也不能留一格多出来的记录：不然下一次返回原地不动，人以为按坏了。
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
afterEach(cleanup)

function mount() {
  const onClose = vi.fn()
  const utils = render(ImageLightbox, {
    props: { src: 'blob:image-1', alt: '图.png', onClose },
    global: { plugins: [createVuetify({ components, directives })] },
  })
  return { onClose, ...utils }
}

/** 等历史退完、popstate 发完。 */
function settled() {
  return new Promise<void>((resolve) => setTimeout(resolve, 20))
}

it('closes on Back and keeps the reader on the page', async () => {
  const { onClose } = mount()
  const href = location.href

  history.back()
  await settled()

  expect(onClose).toHaveBeenCalledTimes(1)
  expect(location.href).toBe(href)
  expect(history.state).toEqual(PAGE)
})

it('leaves no extra history entry behind when closed some other way', async () => {
  const { onClose, unmount } = mount()

  await fireEvent.click(screen.getByRole('button', { name: '关闭' }))
  expect(onClose).toHaveBeenCalledTimes(1)
  // 父组件收到 close 就把这一层拿掉。
  unmount()
  await settled()

  // 回到打开之前那一格：下一次返回就是离开这一页，不会原地空按一下。
  expect(history.state).toEqual(PAGE)
})

it('does not step back a second time after Back already closed it', async () => {
  // 人是从列表页进的房间页：身后还有一格。
  history.replaceState({ page: 'list' }, '')
  history.pushState(PAGE, '')
  const { onClose, unmount } = mount()

  history.back()
  await settled()
  unmount()
  await settled()

  // 只退了自己压的那一格：人还在房间页，没被多退回列表页。
  expect(onClose).toHaveBeenCalledTimes(1)
  expect(history.state).toEqual(PAGE)
})
