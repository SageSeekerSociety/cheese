// 子树渲染崩了：接住、上报一次、换成兜底，并且不让错误再往上冒。
//
// 每一条用例看的都是屏幕上看得见的东西：兜底在不在、子树有没有真的重新挂上、错误有
// 没有漏到父组件的钩子上。重试「重新挂」这件事靠一个挂载计数器说——只有 setup 真的
// 又跑了一次，它才会涨。
import {
  defineComponent,
  ErrorCodes,
  getCurrentInstance,
  h,
  handleError,
  nextTick,
  onErrorCaptured,
  type PropType,
} from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../errorReporter', () => ({ reportError: vi.fn() }))

import { reportError } from '../../errorReporter'

import ErrorBoundary from './ErrorBoundary.vue'

import i18n, { setLocale, t } from '@/i18n'

// 每次 setup 记一笔：重试到底有没有把子树重新挂上，看的就是它。
let mounts = 0
// 下一次挂载会不会抛。重试前放开，模拟「这次好了」。
let boom = true

const Flaky = defineComponent({
  name: 'Flaky',
  setup() {
    mounts += 1
    const fail = boom
    return () => {
      if (fail) throw new Error('boom')
      return h('div', { 'data-testid': 'ok' }, 'ok')
    }
  },
})

// resetKey 要能改，所以套一个宿主拿着它。
const Host = defineComponent({
  props: { resetKey: { type: [String, Number] as PropType<string | number | null>, default: null } },
  setup(props) {
    return () => h(ErrorBoundary, { resetKey: props.resetKey }, { default: () => h(Flaky) })
  },
})

const vuetify = createVuetify({ components, directives })

const mountHost = (resetKey: string | null = null) =>
  render(Host, { props: { resetKey }, global: { plugins: [vuetify, i18n] } })

const fallback = (container: Element) => container.querySelector('.error-boundary')
const ok = (container: Element) => container.querySelector('[data-testid="ok"]')

beforeEach(() => {
  // 这些断言读的是中文界面上的字。
  setLocale('zh-CN')
  mounts = 0
  boom = true
  vi.clearAllMocks()
})

describe('子树渲染出错', () => {
  it('换成兜底：一句话加一颗重试，子树不再显示', async () => {
    const { container } = mountHost()
    await nextTick()

    expect(fallback(container)?.textContent).toContain(t('global.errorBoundary.message'))
    expect(container.querySelector('.error-boundary button')?.textContent).toContain(t('global.errorBoundary.retry'))
    expect(ok(container)).toBeNull()
  })

  it('上报一次，不重复刷屏', async () => {
    mountHost()
    await nextTick()

    expect(reportError).toHaveBeenCalledTimes(1)
    expect(vi.mocked(reportError).mock.calls[0][0]).toBe('boom')
  })

  it('错误不再往父组件冒', async () => {
    const parentHook = vi.fn()
    const Parent = defineComponent({
      setup() {
        onErrorCaptured(parentHook)
        return () => h(ErrorBoundary, null, { default: () => h(Flaky) })
      },
    })
    const { container } = render(Parent, { global: { plugins: [vuetify, i18n] } })
    await nextTick()

    expect(fallback(container), '兜底接住了').toBeTruthy()
    expect(parentHook, '返回 false 之后父组件不该再收到').not.toHaveBeenCalled()
  })

  it('重试把子树重新挂上', async () => {
    const { container } = mountHost()
    await nextTick()
    expect(fallback(container)).toBeTruthy()
    expect(mounts).toBe(1)

    boom = false
    await fireEvent.click(container.querySelector('.error-boundary button')!)
    await nextTick()

    expect(ok(container)).toBeTruthy()
    expect(fallback(container)).toBeNull()
    expect(mounts, 'setup 又跑了一次，换的是新实例').toBe(2)
  })

  it('resetKey 变了就自己恢复，不用点重试', async () => {
    const { container, rerender } = mountHost('topic-a')
    await nextTick()
    expect(fallback(container)).toBeTruthy()

    boom = false
    await rerender({ resetKey: 'topic-b' })
    await nextTick()
    await nextTick()

    expect(fallback(container)).toBeNull()
    expect(ok(container)).toBeTruthy()
  })

  it('异步组件加载失败不当作子树崩了：别收走好着的那半边', async () => {
    const parentHook = vi.fn(() => false)
    // 直接照着 Vue 处理懒加载失败时的样子调 handleError——那一格的 chunk 挂了，Vue 只会
    // 把这一格留空、兄弟照常渲染。把 info 原样带进来，钉住这里不接它。
    const Child = defineComponent({
      setup() {
        handleError(new Error('chunk failed'), getCurrentInstance()!, ErrorCodes.ASYNC_COMPONENT_LOADER)
        return () => h('div', { 'data-testid': 'ok' }, 'ok')
      },
    })
    const Parent = defineComponent({
      setup() {
        onErrorCaptured(parentHook)
        return () => h(ErrorBoundary, null, { default: () => h('div', [h(Child)]) })
      },
    })
    const { container } = render(Parent, { global: { plugins: [vuetify, i18n] } })
    await nextTick()

    expect(fallback(container), '不换兜底，子树照常').toBeNull()
    expect(ok(container), '好着的兄弟还在').toBeTruthy()
    expect(reportError, '不在这里报，交给全局 errorHandler').not.toHaveBeenCalled()
    expect(parentHook, '照样往上冒').toHaveBeenCalled()
    expect((parentHook.mock.calls as unknown[][])[0][2]).toBe('async component loader')
  })

  it('没出错时不插多余的一层：插槽内容就是根', async () => {
    boom = false
    const { container } = mountHost()
    await nextTick()

    // 组件化地包一层，DOM 里不能多出一个壳——否则 flex 布局会当场错位。
    expect(container.firstElementChild?.getAttribute('data-testid'), '第一个元素就是子树自己').toBe('ok')
  })
})
