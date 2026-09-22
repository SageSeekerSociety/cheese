/** 验收卡出现 / 收走是房间里真发生的一件事，要看得出是这里多了一块。
 *
 * 但打开房间时读到的那一张本来就在，不该每次都当着人长出来一遍：只有读完之后再变
 * 的才演。happy-dom 不跑动画，Vue 挂上去的过渡类名就是这里能看到的全部——类名在
 * 就是演了，不在就是直接落在终态上。类名只挂一两帧，所以记的是它**挂过什么**。
 */
import type { AcceptCard, MergeStateInfo } from '../../cx_types'

import { defineComponent, h, ref } from 'vue'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const getAcceptCards = vi.fn()

vi.mock('../../api', async () => {
  const actual = await vi.importActual<typeof import('../../api')>('../../api')
  return {
    ...actual,
    getAcceptCards: (...a: unknown[]) => getAcceptCards(...a),
    getPrChecks: vi.fn().mockResolvedValue({ available: false }),
    getTopic: vi.fn().mockResolvedValue({ id: 't1', status: 'active' }),
  }
})

import TopicAcceptCard from '../TopicAcceptCard.vue'

function mergeState(): MergeStateInfo {
  return {
    state: 'clean',
    who: 'human',
    reasons: [{ kind: 'no_obstacle', checks: [], detail: '可以合并' }],
    head_sha: null,
    checked_at: null,
    since: null,
  }
}

function card(over: Partial<AcceptCard>): AcceptCard {
  return {
    id: 'card-1',
    topic_id: 't1',
    reviewer_handle: 'alice',
    routing_reason: '最懂',
    change_subject: 'chore: do a thing',
    change_body: null,
    status: 'pending',
    decided_by: null,
    decided_at: null,
    note: '',
    note_level: null,
    created_at: '2026-09-01T00:00:00Z',
    gate_passed_at: null,
    gate_output: '',
    approvals: [],
    approvals_required: 1,
    pr_number: null,
    pr_url: null,
    forge: {
      kind: 'forgejo',
      reports_checks: false,
      hosts_proposals: false,
      can_write_remote: false,
      pushes_to_external_remote: false,
      identity: 'platform',
      declaration: '',
    },
    merge_state: mergeState(),
    auto_merge: { allowed: false, armed_by: null, armed_at: null },
    artifact: { id: 'a1', name: '结题报告', version: 4 },
    deliverable: { kind: 'file', filename: '结题报告.docx', url: null },
    ...over,
  } as AcceptCard
}

async function flush() {
  for (let i = 0; i < 8; i += 1) await new Promise((r) => setTimeout(r, 0))
}

/** 房间那一侧：拿着卡的 ref，能像 TopicView 那样叫它重读。 */
let reload: (silent?: boolean) => Promise<void>

function mount() {
  const Host = defineComponent(() => {
    const box = ref<{ reload: (silent?: boolean) => Promise<void> } | null>(null)
    reload = (silent) => box.value!.reload(silent)
    return () => h(TopicAcceptCard, { ref: box, topicId: 't1', topicStatus: 'active' })
  })
  // transition: false —— Vue Test Utils 默认把 <Transition> 换成一个什么都不做的桩，
  // 这份用例要看的正是它挂上去的类名。
  return render(Host, {
    global: { plugins: [createVuetify({ components, directives })], stubs: { transition: false } },
  })
}

/** 这个框从头到尾挂过的所有类名。 */
function watchClasses(container: Element): string[] {
  const seen: string[] = []
  const note = (el: Element) => {
    if (el.classList?.contains('accept-fold')) seen.push(el.className)
  }
  new MutationObserver((records) => {
    for (const r of records) {
      if (r.type === 'attributes') note(r.target as Element)
      r.addedNodes.forEach((n) => n instanceof Element && note(n))
    }
  }).observe(container, { subtree: true, childList: true, attributes: true, attributeFilter: ['class'] })
  return seen
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
})

describe('验收卡的出现', () => {
  it('打开房间时就有的那一张直接在，不演', async () => {
    getAcceptCards.mockResolvedValue({ data: [card({})], has_more: false })
    const { container } = mount()
    const seen = watchClasses(container)
    await flush()

    expect(container.querySelector('.accept-fold')).toBeTruthy()
    expect(seen.join(' ')).not.toContain('accept-fold-enter')
  })

  it('读完之后才递上来的那一张要演出来', async () => {
    getAcceptCards.mockResolvedValue({ data: [], has_more: false })
    const { container } = mount()
    await flush()
    expect(container.querySelector('.accept-fold')).toBeNull()

    // 芝士这一刻递了卡：房间静默重读（TopicView 收到 accept 的状态变更时就这么做）。
    getAcceptCards.mockResolvedValue({ data: [card({})], has_more: false })
    const seen = watchClasses(container)
    await reload(true)
    await flush()

    expect(seen.join(' ')).toContain('accept-fold-enter-active')
  })
})
