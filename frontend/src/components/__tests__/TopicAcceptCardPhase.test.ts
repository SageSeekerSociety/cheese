/** 验收卡只把「这个话题处在哪一段」这一个词交给外面。
 *
 * 话题头靠它决定说哪个状态，工作面板靠它决定开哪一格（`lib/topicState.ts`）。
 * 这一个词有两件事必须成立：卡真的读回来之后才报 —— 读之前「没有卡」和「卡还
 * 没到」在外面长得一模一样，早报一次会让面板开在错误的那一格上；卡收走之后
 * 要再报一次「没有」，否则那一格会停在上一张卡留下的状态里。
 *
 * 所以这里钉的是：读之间什么都不报、读到了是哪一段就报哪一段（含没有卡）、
 * 卡离开之后再报一次没有。
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

import i18n, { setLocale } from '@/i18n'

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

let seq = 0
function card(over: Partial<AcceptCard>): AcceptCard {
  seq += 1
  return {
    id: `card-${seq}`,
    topic_id: 't1',
    reviewer_handle: 'alice',
    focus: '最懂',
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
    ...over,
  } as AcceptCard
}

async function flush() {
  for (let i = 0; i < 8; i += 1) await new Promise((r) => setTimeout(r, 0))
}

/** 房间那一侧：拿着卡的 ref，能像 TopicView 那样叫它重读；报上来的那几段按顺序记着。 */
let reload: () => Promise<void>
let phases: unknown[]

function mount() {
  phases = []
  const Host = defineComponent(() => {
    const box = ref<{ reload: () => Promise<void> } | null>(null)
    reload = () => box.value!.reload()
    return () =>
      h(TopicAcceptCard, {
        ref: box,
        topicId: 't1',
        topicStatus: 'active',
        onPhase: (p: unknown) => phases.push(p),
      })
  })
  return render(Host, { global: { plugins: [createVuetify({ components, directives }), i18n] } })
}

beforeEach(() => {
  // These assertions read the Chinese copy.
  setLocale('zh-CN')
  setActivePinia(createPinia())
  getAcceptCards.mockReset()
})

describe('这一张卡把话题处在哪一段报给外面', () => {
  it('卡还没读回来之前，什么都不往外报', async () => {
    // 一个永远不落地的读：读之前报出去的话，外面会把「还没问过」当成「没有卡」。
    getAcceptCards.mockReturnValue(new Promise(() => {}))
    mount()
    await flush()

    expect(phases).toEqual([])
  })

  it('还没有卡的普通话题报「没有卡」', async () => {
    getAcceptCards.mockResolvedValue({ data: [], has_more: false })
    mount()
    await flush()

    expect(phases).toEqual([null])
  })

  it('有待采纳的卡就报 pending', async () => {
    getAcceptCards.mockResolvedValue({ data: [card({})], has_more: false })
    mount()
    await flush()

    expect(phases).toEqual(['pending'])
  })

  it('历史闸门卡报的是 gate，不是 pending —— 那一段不在等人点采纳', async () => {
    getAcceptCards.mockResolvedValue({ data: [card({ status: 'gate_failed' })], has_more: false })
    mount()
    await flush()

    expect(phases).toEqual(['gate'])
  })

  it('已采纳、合并还没走完的卡报 delivering', async () => {
    getAcceptCards.mockResolvedValue({ data: [card({ status: 'pr_open' })], has_more: false })
    mount()
    await flush()

    expect(phases).toEqual(['delivering'])
  })

  it('卡收走之后再报一次「没有卡」', async () => {
    getAcceptCards.mockResolvedValue({ data: [card({})], has_more: false })
    mount()
    await flush()
    expect(phases).toEqual(['pending'])

    getAcceptCards.mockResolvedValue({ data: [], has_more: false })
    await reload()
    await flush()

    expect(phases).toEqual(['pending', null])
  })
})
