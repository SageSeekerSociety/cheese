/** 成果待采纳框上的字跟着语言走。
 *
 * 这张卡有五张脸（闸门未通过 / 闸门未能执行 / 待采纳 / 已采纳等合并 / 已采纳），
 * 每张脸说的话都不一样，所以这里按脸分别看：中文那几张念的是原话，英文那几张
 * 一个汉字都不许剩。
 *
 * 有一条规矩这里守得比较紧：卡上的 `routing_reason`、`note`、合并依据的
 * `detail` 都是**后端下发的数据**，不是我们写的文案——后端说什么就是什么，中文
 * 英文都可能。所以英文那几例喂的是英文数据，扫的是我们自己画上去的字；中文那
 * 几例照旧用中文数据，顺带证明数据没被动过。
 *
 * 另外，好几个句子里夹着加粗的片段（「<strong>没有结论</strong>」
 * 「<strong>@bob</strong>」），那些句子在词表里被拆成前后两截。Vue 会把元素之间
 * 含换行的空白节点整个去掉，所以拼接严丝合缝——几例按字面把拼出来的整句钉住，
 * 中间多一个空格都算拼错。
 */
import type { AcceptCard, MergeStateInfo, PrChecks } from '../../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const getAcceptCards = vi.fn()
const getPrChecks = vi.fn()

vi.mock('../../api', async () => {
  const actual = await vi.importActual<typeof import('../../api')>('../../api')
  return {
    ...actual,
    getAcceptCards: (...a: unknown[]) => getAcceptCards(...a),
    getPrChecks: (...a: unknown[]) => getPrChecks(...a),
    // 采纳之后组件会连带刷新话题行（store.refreshTopicRow）。它自己吞异常，
    // 但不挡住这里真的去连一个没人监听的端口，报一屏 ECONNREFUSED。
    getTopic: vi.fn().mockResolvedValue({ id: 't1', status: 'active' }),
  }
})

import { setLocale } from '../../i18n'
import TopicAcceptCard from '../TopicAcceptCard.vue'

const CJK = /[㐀-䶿一-鿿豈-﫿]/

beforeEach(() => {
  // 断言的中文都是词表里的 zh-CN 那一边，先把语言钉住：happy-dom 的
  // navigator.language 是 en-US，不钉就是英文。
  setLocale('zh-CN')
  setActivePinia(createPinia())
  getAcceptCards.mockReset()
  getPrChecks.mockReset()
  getPrChecks.mockResolvedValue({ available: false })
})

function mergeState(over: Partial<MergeStateInfo>): MergeStateInfo {
  return {
    state: 'unknown',
    who: 'human',
    reasons: [],
    head_sha: 'abc1234',
    checked_at: '2026-09-06T00:00:00Z',
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
    routing_reason: '',
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
    merge_state: mergeState({ state: 'clean' }),
    auto_merge: { allowed: false, armed_by: null, armed_at: null },
    ...over,
  } as AcceptCard
}

/** 一应俱全的 GitHub lane 待采纳卡：有 PR、有 CI、必跑检查红了（所以采纳灰着、
 *  人工放行那个入口露出来）、绿了自动合已经布防。卡面上能说的话它基本都说了。 */
function richCard(over: Partial<AcceptCard> = {}): AcceptCard {
  return card({
    reviewer_handle: 'alice',
    routing_reason: 'Knows this best',
    has_external_checks: true,
    pr_number: 12,
    pr_url: 'https://github.com/o/r/pull/12',
    approvals: ['bob'],
    approvals_required: 2,
    merge_state: mergeState({
      state: 'blocked',
      who: 'agent',
      reasons: [{ kind: 'required_check_failed', checks: ['test'], detail: 'Required check failed' }],
    }),
    auto_merge: { allowed: true, armed_by: 'bob', armed_at: '2026-09-06T00:00:00Z' },
    ...over,
  })
}

const CI_CHECKS: PrChecks = {
  available: true,
  pr_number: 12,
  mergeable: false,
  checks: [{ name: 'lint', status: 'in_progress', conclusion: null }],
}

async function flush() {
  for (let i = 0; i < 8; i += 1) await new Promise((r) => setTimeout(r, 0))
}

async function mountCard(cards: AcceptCard[], opts: { checks?: PrChecks; topicStatus?: string } = {}) {
  getAcceptCards.mockResolvedValue({ data: cards, has_more: false })
  getPrChecks.mockResolvedValue(opts.checks ?? { available: false })
  const vuetify = createVuetify({ components, directives })
  const utils = render(TopicAcceptCard, {
    props: { topicId: 't1', topicStatus: opts.topicStatus ?? 'active' },
    global: { plugins: [vuetify] },
  })
  await flush()
  return utils
}

const pageText = (container: Element) => (container.textContent ?? '').replace(/\s+/g, ' ')

/** 跟 pageText 不同，这个不折叠空白。三截拼一句话的地方必须用它：
 *  一多一个空格，拼出来就跟原来那句不一样了，而折叠空白正好会把那个空格吃掉。 */
const rawText = (container: Element) => container.textContent ?? ''

describe('讲中文', () => {
  it('待采纳那张脸：等人的那句、按钮、推荐理由、合并态和依据都还是中文', async () => {
    const { container } = await mountCard(
      [
        richCard({
          routing_reason: '最懂',
          merge_state: mergeState({
            state: 'blocked',
            who: 'agent',
            reasons: [{ kind: 'required_check_failed', checks: ['test'], detail: '必跑检查未通过' }],
          }),
        }),
      ],
      { checks: CI_CHECKS }
    )
    const page = pageText(container)

    expect(page).toContain('成果待采纳')
    // 「等 @alice 验收」拆成三个节点（中间那个加粗）。中文这三截之间本来就不带
    // 空格——眼神上那个间隔是 flex 的 ga-1 给的，跟翻译之前一模一样；英文那两截
    // 自带前后空格，合起来才是 "Waiting on @alice to review"。
    expect(page).toContain('等@alice验收')
    expect(page).toContain('推荐理由：最懂')
    expect(page).toContain('合并后的提交标题')
    expect(page).toContain('芝士处理中')
    expect(page).toContain('必跑检查未通过')
    expect(page).toContain('与主分支冲突')
    expect(page).toContain('lint')
    expect(page).toContain('进行中')
    // 批准那一段：计数、批准按钮（名单里没有我）
    expect(page).toContain('1/2 已批准')
    expect(page).toContain('@bob')
    expect(page).toContain('批准')
    expect(page).toContain('去验收')
    expect(page).toContain('采纳')
    expect(page).toContain('退回')
    expect(page).toContain('通过后自动合并')
    expect(page).toContain('由 @bob 开启')
    expect(page).toContain('人工放行并合并')
  })

  it('闸门未能执行那张历史卡：三截拼出来还是完整一句话', async () => {
    const { container } = await mountCard([card({ status: 'gate_blocked', gate_output: 'boom' })])
    const page = pageText(container)

    expect(page).toContain('平台检查未能执行')
    expect(page).not.toContain('平台检查未通过')
    // 这一句在词表里是三截（「没有结论」加了粗），拼出来必须跟原来一模一样。
    expect(page).toContain('检查程序未能启动，因此它对这次改动没有结论——既不是通过，也不是未通过。')
    expect(rawText(container)).toContain('检查程序未能启动，因此它对这次改动没有结论——既不是通过，也不是未通过。')
    expect(page).toContain('查看输出')
  })

  it('闸门未通过那张历史卡：未通过和未能执行分开说', async () => {
    const { container } = await mountCard([card({ status: 'gate_failed', gate_output: 'boom' })])
    const page = pageText(container)
    expect(page).toContain('平台检查未通过')
    expect(page).not.toContain('平台检查未能执行')
    expect(page).toContain('这张验收卡没有送出。')
  })

  it('闸门输出点开就有；没有输出时有兜底', async () => {
    const { container, getByText } = await mountCard([card({ status: 'gate_failed', gate_output: '' })])
    await fireEvent.click(getByText('查看输出'))
    await flush()
    const page = pageText(container)
    expect(page).toContain('收起输出')
    expect(page).toContain('暂无输出')
  })

  it('已采纳等合并那张旧卡：加粗的人是数据，前后两截中文拼得拢', async () => {
    const { container } = await mountCard([card({ status: 'pr_open', decided_by: 'bob' })])
    const page = pageText(container)
    expect(page).toContain('已采纳，未完成合并')
    expect(rawText(container)).toContain('已由 @bob 采纳，但合并没有完成。这是一张旧卡，需要人工处理')
  })

  it('已采纳那张脸：由谁采纳、撤回采纳', async () => {
    const { container } = await mountCard([card({ status: 'accepted', decided_by: 'bob' })], {
      topicStatus: 'archived',
    })
    const page = pageText(container)
    expect(page).toContain('已采纳')
    expect(rawText(container)).toContain('由 @bob 采纳')
    expect(page).toContain('撤回采纳')
  })
})

describe('讲英文', () => {
  it('待采纳那张脸：一个汉字都不剩', async () => {
    setLocale('en')
    const { container } = await mountCard([richCard()], { checks: CI_CHECKS })
    const page = pageText(container)

    expect(page).toContain('Work ready to accept')
    expect(page).toContain('Waiting on @alice to review')
    expect(page).toContain('Why them: Knows this best')
    expect(page).toContain('Commit subject after the merge')
    expect(page).toContain('Cheese is on it')
    expect(page).toContain('Required check failed')
    expect(page).toContain('Conflicts with the main branch')
    expect(page).toContain('lint running')
    expect(page).toContain('1/2 approved')
    expect(page).toContain('@bob')
    expect(page).toContain('Approve')
    expect(page).toContain('Review the work')
    expect(page).toContain('Accept')
    expect(page).toContain('Reject')
    expect(page).toContain('Merge automatically once it passes')
    expect(page).toContain('Enabled by @bob')
    expect(page).toContain('Merge anyway')
    expect(CJK.test(page), page).toBe(false)
  })

  it('闸门未能执行那张历史卡：三截拼出来是通顺的一句英文', async () => {
    setLocale('en')
    const { container } = await mountCard([card({ status: 'gate_blocked', gate_output: 'boom' })])
    const page = pageText(container)
    expect(page).toContain('Platform check could not run')
    // 英文这两截自带前后空格（"…so it says " + "nothing" + " about this change…"），
    // 所以只有不折叠空白的 rawText 才验得出一多一个空格。
    expect(rawText(container)).toContain(
      'The check program never started, so it says nothing about this change — it neither passed nor failed.'
    )
    expect(CJK.test(page), page).toBe(false)
  })

  it('已采纳那两张脸：Accepted by 前后不留双空格', async () => {
    setLocale('en')
    const { container: delivering } = await mountCard([card({ status: 'pr_open', decided_by: 'bob' })])
    expect(rawText(delivering)).toContain('Accepted by @bob, but the merge never finished.')
    expect(CJK.test(pageText(delivering)), pageText(delivering)).toBe(false)

    const { container: accepted } = await mountCard([card({ status: 'accepted', decided_by: 'bob' })], {
      topicStatus: 'archived',
    })
    expect(rawText(accepted)).toContain('Accepted by @bob')
    expect(CJK.test(pageText(accepted)), pageText(accepted)).toBe(false)
  })

  it('切一次语言：已经画出来的字立刻跟着换，灰按钮 title 里那串理由也跟着换', async () => {
    const { container } = await mountCard([richCard()], { checks: CI_CHECKS })
    expect(container.textContent).toContain('成果待采纳')
    expect(container.textContent).toContain('等@alice验收')

    const blockedTitle = () =>
      Array.from(container.querySelectorAll('[title]'))
        .map((e) => e.getAttribute('title') ?? '')
        .find((title) => title.includes('采纳不会合并') || title.includes('will not merge'))
    // 采纳按钮灰着时，为什么灰写在 title 里：状态词 + 后端给的依据，两段都是拼的。
    expect(blockedTitle(), '灰着的采纳按钮要有 title').toContain('现在采纳不会合并：Required check failed')

    setLocale('en')
    await vi.waitFor(() => expect(container.textContent, '卡片标题').toContain('Work ready to accept'))
    const page = pageText(container)
    expect(page).toContain('Waiting on @alice to review')
    expect(page).toContain('Accept')
    expect(CJK.test(page), page).toBe(false)
    expect(blockedTitle(), '换语言后 title 也要换').toContain('Accepting now will not merge: Required check failed')
  })
})
