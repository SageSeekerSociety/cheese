/** 现场读失败时，那一格要留在原地，并给出一条能走的路。
 *
 * 从前的错误态是一条红字，没有按钮：一次超时就只能刷新整页才回得来，而刷新还会
 * 把读者翻到的位置一起丢掉。这里钉两件事——失败时画的是那句「加载失败」加一颗
 * 「重试」，以及按下它真的重新读一次。
 */
import { defineComponent, h } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/vue'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import { setLocale } from '@/i18n'

const api = vi.hoisted(() => ({ getTranscript: vi.fn(), sessionRecordImageUrl: vi.fn() }))
vi.mock('../../api', () => ({ ...api, SITE_PAGE_SIZE: 120 }))
vi.mock('../../composables/useSessionInspector', () => ({ useSessionInspector: () => ({}) }))
// 会话栏只当一格占位：这一条关心的是它下面那一块。
vi.mock('../SessionInspector.vue', () => ({ default: { template: '<div><slot name="actions" /></div>' } }))

import { usePanelSite } from '../../composables/usePanelSite'
import { forgetWindows } from '../../lib/siteWindow'

import PanelSite from './PanelSite.vue'

let vuetify: ReturnType<typeof createVuetify>

beforeAll(() => {
  vuetify = createVuetify({ components, directives })
})

beforeEach(() => {
  setLocale('zh-CN')
  forgetWindows()
  api.getTranscript.mockReset()
  api.sessionRecordImageUrl.mockReset()
})

afterEach(cleanup)

function open() {
  const Host = defineComponent({
    setup() {
      const site = usePanelSite({
        topicId: () => 'room',
        active: () => true,
        refreshTick: () => 0,
        runningTurns: () => ({}),
        agentControl: () => null,
      })
      return () => h(PanelSite, { topicId: 'room', active: true, memberNames: {}, site })
    },
  })
  return render(Host, { global: { plugins: [vuetify] } })
}

describe('a 现场 that could not be read', () => {
  it('says so in place and reads again when the reader asks it to', async () => {
    api.getTranscript.mockRejectedValue(new Error('请求等待超时，请重试'))
    const view = open()

    await waitFor(() => expect(view.container.textContent).toContain('加载失败'))
    expect(view.container.textContent).toContain('请求等待超时，请重试')

    // 重试真的又读了一次，而不是只把红字收起来；读成了这一格就回到正常样子。
    api.getTranscript.mockClear()
    api.getTranscript.mockResolvedValue({ data: [], has_more: false })
    await fireEvent.click(view.getByRole('button', { name: '重试' }))

    await waitFor(() => expect(api.getTranscript).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(view.container.textContent).not.toContain('加载失败'))
  })
})
