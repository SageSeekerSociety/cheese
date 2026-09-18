/** 卡上的状态直接用合并态 (#718)。
 *
 * 状态词和「谁的活」都是后端算好随卡下发的（merge_state.state / who），这里断言
 * 的是屏幕上读得到的翻译不走样：
 *
 *   1. clean 画绿勾、采纳亮；非 clean 的 GitHub lane 卡采纳灰，红了哪个检查
 *      在卡上看得见；
 *   2. 平台 lane（没绑 GitHub，who 恒 human）的卡直接是 CLEAN（#363 拍板：
 *      没有检查可读），画绿勾，采纳从不按状态灰——那里的采纳纯粹是人的判断；
 *   3. 绿了自动合的开关只在项目允许、且卡停在 blocked/behind 时出现，已布防
 *      的卡写明是谁开的，点开关打的是 auto-merge 端点。
 */
import type { AcceptCard, MergeStateInfo } from '../../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const getAcceptCards = vi.fn()
const setAutoMerge = vi.fn()
const acceptCard = vi.fn()

vi.mock('../../api', async () => {
  const actual = await vi.importActual<typeof import('../../api')>('../../api')
  return {
    ...actual,
    getAcceptCards: (...a: unknown[]) => getAcceptCards(...a),
    getPrChecks: vi.fn().mockResolvedValue({ available: false }),
    setAutoMerge: (...a: unknown[]) => setAutoMerge(...a),
    acceptCard: (...a: unknown[]) => acceptCard(...a),
    // 采纳之后组件会连带刷新话题行（store.refreshTopicRow）。它自己吞异常，
    // 但不挡住这里真的去连一个没人监听的端口，报一屏 ECONNREFUSED。
    getTopic: vi.fn().mockResolvedValue({ id: 't1', status: 'active' }),
  }
})

import { setLocale } from '../../i18n'
import TopicAcceptCard from '../TopicAcceptCard.vue'

function mergeState(over: Partial<MergeStateInfo>): MergeStateInfo {
  return {
    state: 'unknown',
    who: 'human',
    reasons: [{ kind: 'no_signal', checks: [], detail: '还没有信号' }],
    head_sha: null,
    checked_at: null,
    since: null,
    ...over,
  }
}

let seq = 0
function card(over: Partial<AcceptCard>): AcceptCard {
  seq += 1
  return {
    id: `card-${seq}`,
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
    has_external_checks: false,
    pr_url: null,
    // 平台 lane 的常态（#363 拍板）：没有检查可读，后端直接下发 clean。
    merge_state: mergeState({
      state: 'clean',
      reasons: [{ kind: 'no_obstacle', checks: [], detail: '可以合并' }],
    }),
    auto_merge: { allowed: false, armed_by: null, armed_at: null },
    ...over,
  } as AcceptCard
}

/** 绑了 GitHub 的卡：有 PR，合并态来自轮询器的镜像。 */
function githubCard(over: Partial<AcceptCard>): AcceptCard {
  return card({ has_external_checks: true, pr_number: 12, pr_url: 'https://github.com/o/r/pull/12', ...over })
}

async function flush() {
  for (let i = 0; i < 8; i += 1) await new Promise((r) => setTimeout(r, 0))
}

async function mountWith(cards: AcceptCard[]) {
  getAcceptCards.mockResolvedValue({ data: cards, has_more: false })
  const vuetify = createVuetify({ components, directives })
  const utils = render(TopicAcceptCard, {
    props: { topicId: 't1', topicStatus: 'active' },
    global: { plugins: [vuetify] },
  })
  await flush()
  return utils
}

function acceptButton(container: Element): HTMLButtonElement {
  const btn = Array.from(container.querySelectorAll('button')).find((b) => b.textContent?.includes('采纳'))
  expect(btn, '采纳按钮应该在卡上').toBeTruthy()
  return btn as HTMLButtonElement
}

beforeEach(() => {
  // 这块卡上的字都走词表了，用例断言的是中文，先把语言钉在 zh-CN 上——
  // happy-dom 的 navigator.language 是 en-US，不钉就是英文。
  setLocale('zh-CN')
  setActivePinia(createPinia())
  getAcceptCards.mockReset()
  setAutoMerge.mockReset()
  acceptCard.mockReset()
})

describe('合的是人看到的那个 commit', () => {
  it('a linked project without a PR offers creation instead of a clean acceptance', async () => {
    const pending = githubCard({
      pr_number: null,
      pr_url: null,
      merge_state: mergeState({
        state: 'unknown',
        who: 'platform',
        reasons: [{ kind: 'no_signal', checks: [], detail: 'PR 尚未创建，检查状态未知' }],
      }),
    })
    const { container, getByRole } = await mountWith([pending])
    const create = getByRole('button', { name: '创建 PR' }) as HTMLButtonElement
    expect(create.disabled).toBe(false)
    expect(container.textContent).toContain('PR 尚未创建，检查状态未知')
    expect(Array.from(container.querySelectorAll('button')).some((b) => b.textContent?.trim() === '采纳')).toBe(false)
    acceptCard.mockRejectedValue({ message: 'PR 已创建，请重新查看提交' })
    await fireEvent.click(create)
    expect(acceptCard).toHaveBeenCalledWith(pending.id, expect.any(String), null)
  })

  it('采纳带上卡面渲染时的那个 head sha', async () => {
    // 轮询器每分钟把卡刷到 PR 的新 head，屏幕上那份不会跟着变。不声明看的是
    // 哪一版，服务端就只能拿数据库里的那个去合——合进去的会是没人看过的代码。
    const seen = 'sha-the-reviewer-actually-read'
    const card = githubCard({
      merge_state: mergeState({
        state: 'clean',
        who: 'human',
        head_sha: seen,
        reasons: [{ kind: 'no_obstacle', checks: [], detail: '可以合并' }],
      }),
    })
    acceptCard.mockResolvedValue({ ...card, status: 'accepted' })
    const { container } = await mountWith([card])

    await fireEvent.click(acceptButton(container))
    await flush()

    expect(acceptCard).toHaveBeenCalledWith(card.id, expect.any(String), seen)
  })
})

describe('卡上的状态直接用合并态', () => {
  it('clean：绿勾 + 可以合并，采纳亮', async () => {
    const { container } = await mountWith([
      githubCard({
        merge_state: mergeState({
          state: 'clean',
          who: 'human',
          reasons: [{ kind: 'no_obstacle', checks: [], detail: '可以合并' }],
        }),
      }),
    ])

    expect(container.textContent).toContain('可以合并')
    expect(acceptButton(container).disabled).toBe(false)
  })

  it('blocked 检查红了：芝士处理中，采纳灰，红了哪个检查看得见', async () => {
    const { container } = await mountWith([
      githubCard({
        merge_state: mergeState({
          state: 'blocked',
          who: 'agent',
          reasons: [{ kind: 'required_check_failed', checks: ['test'], detail: '必跑检查未通过' }],
        }),
      }),
    ])

    expect(container.textContent).toContain('芝士处理中')
    expect(container.textContent).toContain('必跑检查未通过')
    expect(container.textContent).toContain('test')
    expect(acceptButton(container).disabled).toBe(true)
    // 为什么灰写在 title 里，人悬停就能看见。
    expect(container.querySelector('span[title*="现在采纳不会合并"]')).toBeTruthy()
  })

  it('blocked 必跑检查没报到：等 CI', async () => {
    const { container } = await mountWith([
      githubCard({
        merge_state: mergeState({
          state: 'blocked',
          who: 'ci',
          reasons: [{ kind: 'required_check_missing', checks: ['test'], detail: '必跑检查还没报到' }],
        }),
      }),
    ])

    expect(container.textContent).toContain('等 CI')
    expect(acceptButton(container).disabled).toBe(true)
  })

  it('behind：平台更新分支', async () => {
    const { container } = await mountWith([
      githubCard({
        merge_state: mergeState({
          state: 'behind',
          who: 'platform',
          reasons: [{ kind: 'behind_base', checks: [], detail: '落后基线' }],
        }),
      }),
    ])

    expect(container.textContent).toContain('平台更新分支')
    expect(acceptButton(container).disabled).toBe(true)
  })

  it('dirty：芝士处理中', async () => {
    const { container } = await mountWith([
      githubCard({
        merge_state: mergeState({
          state: 'dirty',
          who: 'agent',
          reasons: [{ kind: 'conflict', checks: [], detail: '与基线冲突' }],
        }),
      }),
    ])

    expect(container.textContent).toContain('芝士处理中')
    expect(container.textContent).toContain('与基线冲突')
    expect(acceptButton(container).disabled).toBe(true)
  })

  it('unstable：红的都不在必跑名单——采纳亮，但红了哪个照样念出来', async () => {
    // 按钮亮不亮跟后端的采纳闸门是同一条线：unstable 后端会合，按钮就不能灰
    // （灰着而后端会合，等于把一条走得通的路藏起来）。红了哪个检查、以及它
    // 「不在必跑名单」，都留在按钮上方的依据行里——亮不等于不说。
    const { container } = await mountWith([
      githubCard({
        merge_state: mergeState({
          state: 'unstable',
          who: 'agent',
          reasons: [
            { kind: 'github_verdict', checks: [], detail: '有检查没过，但都不在必跑名单，可以采纳' },
            { kind: 'check_failed', checks: ['lint'], detail: '检查红了：lint' },
          ],
        }),
      }),
    ])

    expect(container.textContent).toContain('芝士处理中')
    expect(container.textContent).toContain('不在必跑名单')
    expect(container.textContent).toContain('lint')
    expect(acceptButton(container).disabled).toBe(false)
  })

  it('blocked 必跑检查在跑：采纳灰——没有结论不是通过', async () => {
    const { container } = await mountWith([
      githubCard({
        merge_state: mergeState({
          state: 'blocked',
          who: 'ci',
          reasons: [{ kind: 'ci_running', checks: ['test'], detail: 'CI 还在跑：test' }],
        }),
      }),
    ])

    expect(container.textContent).toContain('等 CI')
    expect(acceptButton(container).disabled).toBe(true)
  })
})

describe('平台 lane：采纳纯粹是人的判断', () => {
  it('未绑项目的卡直接是 CLEAN：绿勾 + 可以合并，采纳亮（#363 拍板）', async () => {
    const { container } = await mountWith([card({})])

    expect(container.textContent).toContain('可以合并')
    expect(container.querySelector('.mdi-check-circle')).toBeTruthy()
    expect(acceptButton(container).disabled).toBe(false)
    expect(container.textContent).not.toContain('状态更新中')
  })

  it('上次合并撞了冲突的卡照样能点重试', async () => {
    const { container } = await mountWith([
      card({
        status: 'conflict',
        merge_state: mergeState({
          state: 'dirty',
          who: 'human',
          reasons: [{ kind: 'conflict', checks: [], detail: '与基线冲突' }],
        }),
      }),
    ])

    expect(container.textContent).toContain('重试采纳')
    expect(acceptButton(container).disabled).toBe(false)
  })
})

describe('绿了自动合的开关', () => {
  it('项目允许且卡停在 blocked 时出现', async () => {
    const { container } = await mountWith([
      githubCard({
        auto_merge: { allowed: true, armed_by: null, armed_at: null },
        merge_state: mergeState({
          state: 'blocked',
          who: 'ci',
          reasons: [{ kind: 'ci_running', checks: ['test'], detail: '检查在跑' }],
        }),
      }),
    ])

    expect(container.textContent).toContain('通过后自动合并')
  })

  it('项目不允许就不出现，哪怕卡停在 blocked', async () => {
    const { container } = await mountWith([
      githubCard({
        auto_merge: { allowed: false, armed_by: null, armed_at: null },
        merge_state: mergeState({
          state: 'blocked',
          who: 'ci',
          reasons: [{ kind: 'ci_running', checks: ['test'], detail: '检查在跑' }],
        }),
      }),
    ])

    expect(container.textContent).not.toContain('通过后自动合并')
  })

  it('clean 的卡没布防就不出现——没有东西可等', async () => {
    const { container } = await mountWith([
      githubCard({
        auto_merge: { allowed: true, armed_by: null, armed_at: null },
        merge_state: mergeState({
          state: 'clean',
          who: 'human',
          reasons: [{ kind: 'no_obstacle', checks: [], detail: '可以合并' }],
        }),
      }),
    ])

    expect(container.textContent).not.toContain('通过后自动合并')
  })

  it('已布防的卡写明是谁开的，点开关打 auto-merge 端点', async () => {
    const armed = githubCard({
      auto_merge: { allowed: true, armed_by: 'bob', armed_at: '2026-09-06T00:00:00Z' },
      merge_state: mergeState({
        state: 'blocked',
        who: 'ci',
        reasons: [{ kind: 'ci_running', checks: ['test'], detail: '检查在跑' }],
      }),
    })
    setAutoMerge.mockResolvedValue(armed)
    const { container } = await mountWith([armed])

    expect(container.textContent).toContain('由 @bob 开启')

    const input = container.querySelector('.v-switch input[type="checkbox"]') as HTMLInputElement
    expect(input).toBeTruthy()
    input.checked = false
    // Vuetify 的开关把模型更新挂在原生 input 事件上（VSelectionControl.onInput）。
    await fireEvent.input(input)
    await flush()
    // 布防等于提前采纳，所以这个开关也声明「我看的是哪一版」（见「合的是人看到
    // 的那个 commit」那一组）。
    expect(setAutoMerge).toHaveBeenCalledWith(armed.id, false, armed.merge_state.head_sha)
  })
})

describe('人工放行的入口', () => {
  it('GitHub lane 非 clean 时收在小按钮后面', async () => {
    const { container } = await mountWith([
      githubCard({
        merge_state: mergeState({
          state: 'blocked',
          who: 'agent',
          reasons: [{ kind: 'required_check_failed', checks: ['test'], detail: '必跑检查未通过' }],
        }),
      }),
    ])

    expect(container.textContent).toContain('人工放行并合并')
  })

  it('clean 的卡没有它——正门就是开的', async () => {
    const { container } = await mountWith([
      githubCard({
        merge_state: mergeState({
          state: 'clean',
          who: 'human',
          reasons: [{ kind: 'no_obstacle', checks: [], detail: '可以合并' }],
        }),
      }),
    ])

    expect(container.textContent).not.toContain('人工放行并合并')
  })

  it('平台 lane 没有它——那里没有 PR 可放行', async () => {
    const { container } = await mountWith([card({})])

    expect(container.textContent).not.toContain('人工放行并合并')
  })
})
