/** 导览的「采纳」那一步：卡上真有「采纳 / 退回」时才算到。卡刚递来、合并态还在
 * 「状态更新中」时，审阅人点不了什么，导览不能指着它说「点采纳」。 */
import type { AcceptCard } from '@/cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const getAcceptCards = vi.fn()

vi.mock('@/api/reviewComments', () => ({
  listReviewComments: vi.fn(async () => ({ comments: [] })),
  writeReviewComment: vi.fn(),
  editReviewComment: vi.fn(),
  deleteReviewComment: vi.fn(),
}))
vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return {
    ...actual,
    getAcceptCards: (...a: unknown[]) => getAcceptCards(...a),
    getPrChecks: vi.fn().mockResolvedValue({ available: false }),
  }
})

import { __resetGuideAnchorsForTests, guideAnchor } from '@/composables/useGuideAnchors'

import TopicAcceptCard from './TopicAcceptCard.vue'

import i18n, { setLocale } from '@/i18n'
import { useWorkspaceStore } from '@/stores/workspace'

/** 绑了 GitHub 的仓库递来的一张卡，审阅人是 alice。 */
function card(state: 'unknown' | 'clean'): AcceptCard {
  return {
    id: 'card-1',
    topic_id: 't1',
    reviewer_handle: 'alice',
    status: 'pending',
    note: '',
    approvals: [],
    merge_state: {
      state,
      who: state === 'clean' ? 'human' : 'platform',
      reasons: [],
      head_sha: 'abc',
      checked_at: null,
      since: null,
    },
    forge: { kind: 'github', reports_checks: true, hosts_proposals: true, declaration: '' },
    auto_merge: { allowed: false, armed_by: null },
    pr_number: 7,
  } as unknown as AcceptCard
}

async function flush() {
  for (let i = 0; i < 6; i += 1) await new Promise((r) => setTimeout(r, 0))
}

function mount() {
  return render(TopicAcceptCard, {
    props: { topicId: 't1', topicStatus: 'active', taskId: 'k1', viewer: 'alice' },
    global: { plugins: [i18n, createVuetify({ components, directives })] },
  })
}

beforeEach(() => {
  setLocale('zh-CN')
  setActivePinia(createPinia())
  __resetGuideAnchorsForTests()
  getAcceptCards.mockReset()
  useWorkspaceStore().refreshTopicRow = vi.fn().mockResolvedValue(undefined)
})

describe('the guide’s accept step', () => {
  it('does not arrive while the card is still settling and nothing can be decided', async () => {
    getAcceptCards.mockResolvedValue({ data: [card('unknown')] })
    mount()
    await flush()
    expect(guideAnchor('accept-card')).toBeNull()
  })

  it('arrives once the reviewer can accept or send it back', async () => {
    getAcceptCards.mockResolvedValue({ data: [card('clean')] })
    const view = mount()
    await flush()
    expect(guideAnchor('accept-card')).not.toBeNull()
    expect(view.getByRole('button', { name: /采纳/ })).toBeTruthy()
  })
})
