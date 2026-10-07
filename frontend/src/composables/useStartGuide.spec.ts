/** 上手引导的两个把手：跳过记在哪、锚点登记表怎么进出。
 *
 *  「谁登记了、谁注销了」是这套东西唯一的机制：气泡之所以能只画在「目标此刻真的在
 *  页面上」的时候，靠的就是这张表。指令挂在组件上（`<v-btn v-guide-anchor="...">`）
 *  而不是模板 ref 上，所以这里连挂组件的那种用法一起锁上。
 */
import type { Ref } from 'vue'

import { defineComponent, h, ref, withDirectives } from 'vue'
import { render } from '@testing-library/vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  __resetStartGuideForTests,
  guideAnchor,
  useGuideAnchorRevision,
  useStartGuide,
  vGuideAnchor,
} from './useStartGuide'

/** 一颗挂着指令的按钮。名字是 ref，好试「同一个元素改登记成别的名字」。 */
function anchorHost(name: Ref<string>) {
  return defineComponent(
    () => () => withDirectives(h('button', { 'data-testid': 'target', type: 'button' }), [[vGuideAnchor, name.value]])
  )
}

beforeEach(() => {
  localStorage.clear()
  __resetStartGuideForTests()
})
afterEach(() => {
  localStorage.clear()
  __resetStartGuideForTests()
})

describe('跳过', () => {
  it('点一次就跳过，并且记在这台机器上', () => {
    const { skipped, skip } = useStartGuide()
    expect(skipped.value).toBe(false)
    skip()
    expect(skipped.value).toBe(true)
    expect(localStorage.getItem('cheese.startGuide.skipped')).toBe('1')
  })

  it('下次开一个会话（模块重新读一遍）还是跳过的', async () => {
    useStartGuide().skip()

    vi.resetModules()
    const fresh = await import('./useStartGuide')
    expect(fresh.useStartGuide().skipped.value).toBe(true)
  })

  it('读不到时当没跳过：多提示一次，好过永远不再提示', () => {
    const spy = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('无痕模式')
    })
    vi.resetModules()
    return import('./useStartGuide').then((fresh) => {
      expect(fresh.useStartGuide().skipped.value).toBe(false)
      spy.mockRestore()
    })
  })
})

describe('锚点登记表', () => {
  it('挂上就登记，卸下就不指了', () => {
    const name = ref('composer-attach')
    const view = render(anchorHost(name))
    const target = view.getByTestId('target')

    expect(guideAnchor('composer-attach')).toBe(target)

    view.unmount()
    expect(guideAnchor('composer-attach')).toBeNull()
  })

  it('同一个元素改登记成别的名字：旧名字不再指它', async () => {
    const name = ref('rail-add')
    const view = render(anchorHost(name))
    const target = view.getByTestId('target')
    expect(guideAnchor('rail-add')).toBe(target)

    name.value = 'new-project'
    await Promise.resolve()
    expect(guideAnchor('new-project')).toBe(target)
    expect(guideAnchor('rail-add')).toBeNull()

    view.unmount()
  })

  it('名字是空的就不登记——「这一步没有目标」不该占住一个名字', () => {
    const name = ref('')
    const view = render(anchorHost(name))
    expect(guideAnchor('')).toBeNull()
    view.unmount()
  })

  it('换一步、换页面时登记表改过几回，浮层据此重新找一次目标', async () => {
    const revision = useGuideAnchorRevision()
    const before = revision.value

    const name = ref('composer-input')
    const view = render(anchorHost(name))
    expect(revision.value).toBeGreaterThan(before)

    const afterMount = revision.value
    name.value = 'composer-attach'
    await Promise.resolve()
    expect(revision.value).toBeGreaterThan(afterMount)

    view.unmount()
    expect(revision.value).toBeGreaterThan(afterMount)
  })
})
