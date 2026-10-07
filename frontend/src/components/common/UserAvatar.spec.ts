/**
 * 头像的加载契约。
 *
 * 后端对「表里有行、盘上没文件」的头像回 404，而那个 404 上不带缓存头，浏览器
 * 缓存里没有可复用的响应。断言钉的就是这一条链子的两头：
 *
 * 1. 取不到的 URL 在**本次会话**里只问一次。第一次仍然要请求（不预判），错了就
 *    记下来，之后直接画彩色首字母 —— 不再造 `<img>`，也就不会再发那次注定失败的
 *    请求。记忆是模块级的 `Set`，随页面加载生灭，所以这里用不同的 URL 分测试，
 *    互不污染。
 * 2. 能取到的 URL 每次进入都照常请求：失败才需要记忆，成功有后端一年的
 *    `Cache-Control` 兜着，不该多一层。
 *
 * 计数钉在 `img.src` 的赋值上，而不是「DOM 里有没有 img」：浏览器对失效响应不
 * 会自动重试，所以真正决定「发不发请求」的是有没有一个**新的** `<img>` 被指向
 * 这个 URL —— 也就是有没有再次赋值。
 */
import { nextTick } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import UserAvatar from './UserAvatar.vue'

const vuetify = createVuetify({ components, directives })

/** 每次 `img.src` 赋值即一次请求（或一次缓存命中），把它们记下来。 */
function trackImageSources() {
  const requested: string[] = []
  const original = Object.getOwnPropertyDescriptor(HTMLImageElement.prototype, 'src')!
  Object.defineProperty(HTMLImageElement.prototype, 'src', {
    configurable: true,
    get: original.get,
    set(value: string) {
      requested.push(value)
      original.set!.call(this, value)
    },
  })
  return {
    requested,
    restore: () => Object.defineProperty(HTMLImageElement.prototype, 'src', original),
  }
}

let requested: string[] = []
let restoreSrc: () => void = () => {}

beforeEach(() => {
  const tracked = trackImageSources()
  requested = tracked.requested
  restoreSrc = tracked.restore
})

afterEach(() => {
  restoreSrc()
})

function mount(props: Record<string, unknown>) {
  return render(UserAvatar, {
    props: { name: 'andy', ...props },
    global: { plugins: [vuetify] },
  })
}

describe('UserAvatar 的加载与失败记忆', () => {
  it('能取到的头像，每次进入都照常请求', async () => {
    const url = 'http://api.test/avatars/11'

    const first = mount({ avatar: url })
    await nextTick()
    expect(requested).toEqual([url])
    first.unmount()

    // 切走再回来：新组件、新 `<img>`，请求照发 —— 成功路径不该被记忆短路。
    const second = mount({ avatar: url })
    await nextTick()
    expect(requested).toEqual([url, url])
    expect(second.container.querySelector('img')?.getAttribute('src')).toBe(url)
    second.unmount()
  })

  it('取不到的头像，本次会话不再请求第二次', async () => {
    const url = 'http://api.test/avatars/12'

    const first = mount({ avatar: url })
    await nextTick()
    expect(requested, '第一次进来还是要去问一次，不能预判').toEqual([url])
    const img = first.container.querySelector('img')
    expect(img, '没有 `<img>` 就没有那次请求').not.toBeNull()
    img!.dispatchEvent(new Event('error'))
    await nextTick()
    // 失败的当帧先回落成彩色首字母，但留在原地（没重发请求）。
    expect(first.container.querySelector('.user-avatar-char')?.textContent).toBe('A')
    expect(requested).toEqual([url])
    first.unmount()

    // 再进一次：已知取不到，不该再造 `<img>`，直接首字母。
    const second = mount({ avatar: url })
    await nextTick()
    expect(requested, '已知取不到的 URL 不该再发一次请求').toEqual([url])
    expect(second.container.querySelector('img')).toBeNull()
    expect(second.container.querySelector('.user-avatar-char')?.textContent).toBe('A')
    second.unmount()
  })

  it('默认对读屏是装饰性的，传 alt 才让头像自己说话', () => {
    const decorative = mount({ avatar: '' })
    const a = decorative.container.querySelector('.v-avatar')!
    expect(a.getAttribute('aria-hidden')).toBe('true')
    expect(a.getAttribute('role')).toBeNull()
    expect(a.getAttribute('aria-label')).toBeNull()
    decorative.unmount()

    const labelled = mount({ avatar: '', alt: 'andy 的头像' })
    const b = labelled.container.querySelector('.v-avatar')!
    expect(b.getAttribute('aria-hidden')).toBeNull()
    expect(b.getAttribute('role')).toBe('img')
    expect(b.getAttribute('aria-label')).toBe('andy 的头像')
    labelled.unmount()
  })
})

/**
 * 形状（§3.14）：人是圆，团队、空间、项目是圆角方块，半径是边长的四分之一。
 *
 * 这一条盯的是一个**只有 CSS 才看得见**的坑：Vuetify 的 `false` 意思是「方角」，它会挂
 * `rounded-0`，而 `.rounded-0 { border-radius: 0 !important }` 压得过组件写给 `v-avatar`
 * 的内联圆角 —— 于是圆角方块全成直角（团队、空间、项目一起），而 DOM 里看不出来：内联
 * 样式照样写着 12px，只有那一个类名露馅。所以钉的是类名 + 内联样式这两头。
 */
describe('UserAvatar 的形状', () => {
  it('人用 Vuetify 的圆；org 绝不能沾上它的方角', () => {
    const person = mount({ avatar: '', kind: 'person', size: 32 })
    const round = person.container.querySelector('.v-avatar')!
    expect(round.classList.contains('rounded-circle')).toBe(true)
    expect(round.classList.contains('rounded-0')).toBe(false)
    person.unmount()

    const org = mount({ avatar: '', kind: 'org', size: 48 })
    const square = org.container.querySelector('.v-avatar')!
    // 方角在 Vuetify 里就是 `rounded-0`（border-radius: 0 !important）——它一出现，圆角就没了。
    expect(square.classList.contains('rounded-0'), '方角会吃掉圆角').toBe(false)
    // 圆角由内联样式说：48px → 12px（squareRadius 是边长四分之一）。
    expect(square.getAttribute('style')).toContain('border-radius: 12px')
    org.unmount()
  })
})
