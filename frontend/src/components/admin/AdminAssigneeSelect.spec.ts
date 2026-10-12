/**
 * 「指派给谁」那一格（`AdminAssigneeSelect.vue`）：候选与选中值都要画**人**。
 *
 * 这里钉住两件会静默坏掉的事：
 *
 * 1. **候选一行只写一次名字**。Vuetify 的 `v-list-item` 在 `title` 有值时会自己画一行
 *    标题，而这一格又在默认插槽里塞了自己的名字 —— 不清掉 `title` 就画两遍（同一行
 *    「鲍勃 / 鲍勃」）。判据是那一行里 `.v-list-item-title` 只有一个。
 * 2. **选中值那格画名字 + 头像**，不是把 handle 原样贴上去。搜的是账号，但人认的是昵称；
 *    一格里写 `bob` 和写「鲍勃」对人来说是两个人名。
 *
 * 取数走 `searchAdminCandidates`，这里 mock 掉；防抖 250ms，所以断言都在 `waitFor` 里
 * 等它到货，不等就断言等于在测「还没画出来的那一帧」。
 */
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const searchAdminCandidates = vi.fn()

vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return {
    ...actual,
    searchAdminCandidates: (...a: unknown[]) => searchAdminCandidates(...a),
  }
})

import AdminAssigneeSelect from './AdminAssigneeSelect.vue'

import i18n, { setLocale } from '@/i18n'

const vuetify = createVuetify({ components, directives })

const CANDIDATES = [{ handle: 'bob', nickname: '鲍勃', avatar_id: 5, already_admin: false }]

/** VMenu 开浮层要 `visualViewport` 和 `devicePixelRatio`，happy-dom 没有（同 TaskHeader）。 */
function stubViewport() {
  vi.stubGlobal('devicePixelRatio', 1)
  vi.stubGlobal('visualViewport', {
    width: 1024,
    height: 768,
    offsetLeft: 0,
    offsetTop: 0,
    addEventListener() {},
    removeEventListener() {},
  })
}

function mount(props: Record<string, unknown> = {}) {
  return render(AdminAssigneeSelect, {
    props: { modelValue: null, ...props },
    global: { plugins: [vuetify, i18n] },
  })
}

beforeEach(() => {
  setLocale('zh-CN')
  searchAdminCandidates.mockReset()
})

describe('指派选择器', () => {
  it('选中值那格画的是名字 + 头像，不是一行纯 handle', async () => {
    searchAdminCandidates.mockResolvedValue({ items: CANDIDATES })
    const view = mount({ modelValue: 'bob' })

    const input = view.container.querySelector('input') as HTMLInputElement
    await fireEvent.update(input, 'bob')

    await waitFor(() => {
      const sel = view.container.querySelector('.v-autocomplete__selection .identity-view')
      expect(sel?.textContent).toContain('鲍勃')
    })
  })

  it('候选一行只写一次名字（不重复标题）', async () => {
    stubViewport()
    searchAdminCandidates.mockResolvedValue({ items: CANDIDATES })
    const view = mount()

    // 候选画在浮层菜单里（`openOnClick: false`）：先按下那一格展开，再打字，和真人用
    // 这一格的顺序一样（先点进去再输）。反过来（先打字后展开）在 happy-dom 里菜单开着
    // 却是空的 —— 展开那一下会把搜索串清掉（VSelect 的 `onAfterLeave`）。
    const box = view.container.querySelector<HTMLElement>('[role="combobox"]')
    await fireEvent.mouseDown(box!)
    const input = view.container.querySelector('input') as HTMLInputElement
    await fireEvent.update(input, 'bob')
    await waitFor(() => {
      expect(document.querySelector('.v-overlay--active .v-list-item')).not.toBeNull()
    })
    const item = document.querySelector('.v-overlay--active .v-list-item') as HTMLElement
    // 名字只画一次：清掉 `title` 之后 Vuetify 自己不再画一行标题，只剩 IdentityView 那一行。
    expect(item.textContent?.match(/鲍勃/g)?.length).toBe(1)
    expect(item.querySelector('.identity-view')).not.toBeNull()
    // 头像对读屏是装饰（名字就在旁边），和别处同一套。
    expect(item.querySelector('.identity-view__face')?.getAttribute('aria-hidden')).toBe('true')
  })
})
