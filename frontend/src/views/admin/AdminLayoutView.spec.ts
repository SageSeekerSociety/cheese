/**
 * 外壳**画的那一半**：门的三态，加上快捷键表的开合。
 *
 * 门是三态而不是两态 —— 还没有答案（`metaChecked` 为假，正在问）和问到了但不让进
 * （`canEnter` 为假）都画在门口，但话不一样：把「还在问」画成「不给进」，读的人会以为
 * 被拒了；反过来把一次拒绝画成一次等待，就把该看见的挡在外面。这一组两头都钉。
 *
 * 取数（`loadMeta` / `refreshCounts`）、读地址、全局键都在容器 `AdminLayout.vue` 里；
 * 这里只吃 props、只往上发事件，所以能单独挂起来看。
 */
import { defineComponent } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { describe, expect, it, vi } from 'vitest'

// 词条本身对不对由 `i18n/catalog.spec.ts` 管；这一组问的是走了哪一档。
vi.mock('vue-i18n', async () => {
  const actual = await vi.importActual<typeof import('vue-i18n')>('vue-i18n')
  return {
    ...actual,
    useI18n: () => ({ t: (key: string) => key }),
  }
})

import AdminLayoutView from './AdminLayoutView.vue'

/** 快捷键表那一片的替身：点一下往上发一次「关」。 */
const SheetStub = defineComponent({
  props: { modelValue: { type: Boolean, default: false } },
  emits: ['update:modelValue'],
  template: `<button class="stub-sheet" @click="$emit('update:modelValue', false)">sheet</button>`,
})

async function mount(props: Record<string, unknown> = {}) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/', component: { template: '<div>子页</div>' } }],
  })
  await router.push('/')
  await router.isReady()
  const vuetify = createVuetify({ components, directives })
  return render(AdminLayoutView, {
    props: { metaChecked: true, canEnter: true, shortcutOpen: false, ...props },
    global: { plugins: [vuetify, router], stubs: { AdminShortcutSheet: SheetStub } },
  })
}

describe('外壳页的画面', () => {
  it('还没有答案时画「正在检查」，不画成拒绝，也不画内容', async () => {
    const { getByText, queryByText } = await mount({ metaChecked: false })

    expect(getByText('admin.layout.checking')).toBeTruthy()
    expect(queryByText('admin.layout.deniedTitle')).toBeNull()
    expect(queryByText('子页')).toBeNull()
  })

  it('问到了但不让进时画拒绝和去反馈中心，不给内容', async () => {
    const { getByText, queryByText } = await mount({ canEnter: false })

    expect(getByText('admin.layout.deniedTitle')).toBeTruthy()
    expect(getByText('admin.layout.deniedBody')).toBeTruthy()
    expect(getByText('admin.layout.toFeedbackCenter')).toBeTruthy()
    expect(queryByText('admin.layout.checking')).toBeNull()
    expect(queryByText('子页')).toBeNull()
  })

  it('过门以后画的是子路由', async () => {
    const { getByText, queryByText } = await mount()

    expect(getByText('子页')).toBeTruthy()
    expect(queryByText('admin.layout.deniedTitle')).toBeNull()
  })

  it('快捷键表把「关」往上发', async () => {
    const { emitted, getByText } = await mount({ shortcutOpen: true })

    await fireEvent.click(getByText('sheet'))
    expect(emitted('update:shortcutOpen')).toEqual([[false]])
  })
})
