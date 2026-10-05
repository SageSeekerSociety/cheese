/**
 * 看板这一页**画的那一半**：读失败那整块、按分类切屏，以及几处向上发的意图。
 *
 * 取数（分类缓存、窗口、轮询、时效戳、重试）在容器 `AdminDashboardPage.vue` 和
 * `composables/useAdminDashboard.ts` 里；这里只吃 props、只往上发事件。出错是**整块**
 * 换掉（页头留着 —— 它是这一页的名字，不是数据），不是在某屏里塞一行红字。
 */
import { defineComponent } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { describe, expect, it, vi } from 'vitest'

vi.mock('vue-i18n', async () => {
  const actual = await vi.importActual<typeof import('vue-i18n')>('vue-i18n')
  return {
    ...actual,
    useI18n: () => ({ t: (key: string) => key }),
  }
})

import AdminDashboardPageView from './AdminDashboardPageView.vue'

/** 每一屏的替身：把收到的名字画出来，好断言切到了哪一屏。 */
function screenStub(label: string) {
  return defineComponent({ template: `<div class="stub-screen">${label}</div>` })
}

/** 页头替身：按一下往上发一次「换窗口」。 */
const HeaderStub = defineComponent({
  props: { windowed: Boolean, days: Number, stamp: String },
  emits: ['setDays'],
  template: `<button class="stub-header" @click="$emit('setDays', 30)">header</button>`,
})

/** 分类导轨替身：按一下往上发一次「切到 usage」。 */
const KindsStub = defineComponent({
  props: { kinds: Array, tabs: Object, titles: Object, pulse: Object, current: String },
  emits: ['select'],
  template: `<button class="stub-kinds" @click="$emit('select', 'usage')">kinds</button>`,
})

/** 反馈屏替身：按一下往上发一次「点了某一天」。 */
const FeedbackStub = defineComponent({
  props: { data: Object, pending: Array, listLoading: Boolean, days: Number, loading: Boolean },
  emits: ['selectDay'],
  template: `<button class="stub-feedback" @click="$emit('selectDay', '2026-01-02')">feedback</button>`,
})

const BASE = {
  kinds: ['pipeline', 'usage'],
  tabs: {},
  titles: {},
  pulse: {},
  kind: 'pipeline',
  days: 7,
  windowed: false,
  loading: false,
  error: null,
  failed: false,
  stamp: '2026-10-05 12:00Z',
  pending: [],
  listLoading: false,
  pipeline: null,
  product: null,
  feedback: null,
  usage: null,
  platform: null,
  performance: null,
  integrations: null,
}

function mount(props: Record<string, unknown> = {}) {
  return render(AdminDashboardPageView, {
    props: { ...BASE, ...props },
    global: {
      plugins: [createVuetify({ components, directives })],
      stubs: {
        AdminDashboardHeader: HeaderStub,
        AdminDashboardKinds: KindsStub,
        AdminDashboardPipeline: screenStub('pipeline'),
        AdminDashboardProduct: screenStub('product'),
        AdminDashboardIntegrations: screenStub('integrations'),
        AdminDashboardFeedback: FeedbackStub,
        AdminDashboardUsage: screenStub('usage'),
        AdminDashboardPerformance: screenStub('performance'),
        AdminDashboardPlatform: screenStub('platform'),
      },
    },
  })
}

describe('看板页的画面', () => {
  it('读失败时画整块错误和重试，不画任何一屏', () => {
    const { getByText, queryByText } = mount({ failed: true, kind: 'pipeline' })

    expect(getByText('feedback.dashboard.error.title')).toBeTruthy()
    expect(getByText('feedback.dashboard.retry')).toBeTruthy()
    expect(queryByText('pipeline')).toBeNull()
  })

  it('读失败时重试那颗按钮往上发一次 retry', async () => {
    const { emitted, getByText } = mount({ failed: true })

    await fireEvent.click(getByText('feedback.dashboard.retry'))
    expect(emitted('retry')).toEqual([[]])
  })

  it('按 kind 画对应的那一屏', () => {
    const { getByText, queryByText } = mount({ kind: 'usage' })

    expect(getByText('usage')).toBeTruthy()
    expect(queryByText('pipeline')).toBeNull()
  })

  it('页头换窗口的意图往上发', async () => {
    const { emitted, getByText } = mount()

    await fireEvent.click(getByText('header'))
    expect(emitted('setDays')).toEqual([[30]])
  })

  it('分类导轨切屏的意图往上发', async () => {
    const { emitted, getByText } = mount()

    await fireEvent.click(getByText('kinds'))
    expect(emitted('select')).toEqual([['usage']])
  })

  it('反馈屏点某一天的意图往上发', async () => {
    const { emitted, getByText } = mount({ kind: 'feedback' })

    await fireEvent.click(getByText('feedback'))
    expect(emitted('selectDay')).toEqual([['2026-01-02']])
  })
})
