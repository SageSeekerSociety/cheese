// 这个组件自己的那几句提示（加载中、没有更多了、加载更多）原来写死在模板里，
// 用它的一页页翻完了也还是中文。这里盯的是它四段默认内容的语言：调用方多半不
// 传这几个插槽，屏幕上看到的就是这里的默认值。
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, render } from '@testing-library/vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import InfiniteScroll from './InfiniteScroll.vue'

import i18n, { setLocale } from '@/i18n'

const vuetify = createVuetify({ components, directives })
const CJK = /[㐀-䶿一-鿿豈-﫿]/

function mount(props: Record<string, unknown>) {
  return render(InfiniteScroll, {
    props: { loading: false, hasMore: false, ...props },
    global: { plugins: [vuetify, i18n] },
  })
}

afterEach(cleanup)

describe('infinite scroll chrome', () => {
  it('says its four states in the language on screen', () => {
    setLocale('en')
    expect(mount({ initialLoading: true }).container.textContent).toContain('Loading...')
    expect(mount({ isEmpty: true }).container.textContent).toContain('No data')
    expect(mount({ loading: true }).container.textContent).toContain('Loading...')
    expect(mount({ hasMore: true, forceManual: true }).container.textContent).toContain('Load more')
    expect(mount({ hasMore: false }).container.textContent).toContain('No more')
  })

  it('leaves no Chinese in any of them', () => {
    setLocale('en')
    for (const props of [
      { initialLoading: true },
      { isEmpty: true },
      { loading: true },
      { hasMore: true, forceManual: true },
      { hasMore: false },
    ]) {
      const view = mount(props)
      expect(view.container.textContent ?? '').not.toMatch(CJK)
      cleanup()
    }
  })

  it('还是说原来的那几句中文', () => {
    setLocale('zh-CN')
    expect(mount({ initialLoading: true }).container.textContent).toContain('加载中...')
    expect(mount({ isEmpty: true }).container.textContent).toContain('暂无数据')
    expect(mount({ hasMore: true, forceManual: true }).container.textContent).toContain('加载更多')
    expect(mount({ hasMore: false }).container.textContent).toContain('没有更多了')
  })
})

// 组件挂载时会往 window 上挂 scroll 监听，卸载时要摘掉；游离的监听会让 nextTick
// 里的 checkContentFull 读到已经不存在的节点。这里只是把它跑一遍。
describe('listener lifecycle', () => {
  it('detaches on unmount', () => {
    const remove = vi.spyOn(window, 'removeEventListener')
    const view = mount({})
    view.unmount()
    expect(remove).toHaveBeenCalledWith('scroll', expect.any(Function))
    remove.mockRestore()
  })
})
