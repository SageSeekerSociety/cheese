/**
 * 「棘轮」这一页的**取数层**：`GET /admin/ratchet` 读存档，`POST /admin/ratchet/refresh` 去 CI
 * 拉一次。画面那一半在 `AdminRatchetPageView.spec.ts` 里，这里只问状态怎么流转。
 *
 * 三条，都是「这一层自己会犯、而视图看不见」的错：
 *
 * 1. **读失败之后刷新成功，`failed` 必须清掉。** 原来不清，于是视图那一支继续按「读不出来」
 *    画——手上明明已经有整页数据，屏幕上却还是一句错误。这一次是真的发生过。
 * 2. **刷新自己失败（服务端返回 200 而 `refresh.error` 有值）说的是「这次拉取的结果」**，
 *    不是「读不出来」，也不是「没有存档」：拉不到的时候，已经存下的那些点还是真的。
 * 3. **刷新整个请求挂掉**（网络/服务端）也要说成「这次没拉到」，并且不能把已有的画面抹掉。
 */
import type { RatchetBoard } from '@/views/admin/ratchetApi'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render, waitFor } from '@testing-library/vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const get = vi.fn()
const refresh = vi.fn()
vi.mock('@/views/admin/ratchetApi', () => ({
  getRatchetBoard: (...args: unknown[]) => get(...args),
  refreshRatchetBoard: (...args: unknown[]) => refresh(...args),
}))

// 带参数的键把参数一起拼进去：「拉到了几份」「没拉到的原因是什么」都在断言里看得见。
vi.mock('vue-i18n', async () => {
  const actual = await vi.importActual<typeof import('vue-i18n')>('vue-i18n')
  return {
    ...actual,
    useI18n: () => ({
      t: (key: string, params?: Record<string, unknown>) => (params ? `${key} ${JSON.stringify(params)}` : key),
    }),
  }
})

import AdminRatchetPage from './AdminRatchetPage.vue'

const BOARD: RatchetBoard = {
  repo: 'SageSeekerSociety/cheese',
  generated_at: '2026-09-30T13:00:00Z',
  deployed_commit: 'dc069a4aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
  collected_commit: 'bbbbbbb2aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
  collected_at: '2026-09-30T11:00:00+00:00',
  run_url: 'https://example.test/run/2',
  collection: 'ok',
  points: 2,
  total_stored: 2,
  collections: [
    { commit: 'aaaaaaa1', collected_at: null, run_url: '', collection: 'ok', reason: null },
    { commit: 'bbbbbbb2', collected_at: null, run_url: '', collection: 'ok', reason: null },
  ],
  areas: [
    {
      area: 'boundaries',
      checks: [
        {
          id: 'fe-boundary',
          area: 'boundaries',
          better: 'down',
          direction: 'flat',
          status: 'pass',
          actual: 3,
          frozen: 3,
          stale: [],
          stale_count: null,
          rule_fingerprint: 'fp-a',
          points: [
            {
              commit: 'bbbbbbb2',
              collected_at: null,
              run_url: '',
              collection: 'ok',
              status: 'pass',
              actual: 3,
              frozen: 3,
              stale_count: null,
              rule_fingerprint: 'fp-a',
              rule_changed: false,
              new_exemptions: null,
              details: null,
              reason: null,
            },
          ],
        },
      ],
    },
  ],
}

function mount() {
  return render(AdminRatchetPage, {
    global: { plugins: [createVuetify({ components, directives })] },
  })
}

/** 把视图换成一面镜子：这一层交给视图的就是这几个 prop，照原样摆出来。
 *  为什么要单独看 prop 而不是看画面：视图那一支自己也有 `!board` 挡着，所以
 *  「刷新成功后 failed 清零」这件事在画面上是**看不见的**——可它是这一层的契约，
 *  少了它，视图就只剩一道防线。 */
function mirror() {
  return render(AdminRatchetPage, {
    global: {
      plugins: [createVuetify({ components, directives })],
      stubs: {
        AdminRatchetPageView: {
          props: ['board', 'loading', 'failed', 'pull', 'pullFailed', 'refreshing'],
          emits: ['refresh', 'retry'],
          template:
            '<div><b data-mirror="failed">{{ String(failed) }}</b>' +
            '<button type="button" @click="$emit(\'refresh\')">pull</button></div>',
        },
      },
    },
  })
}

beforeEach(() => {
  get.mockReset()
  refresh.mockReset()
})

describe('棘轮页的取数层', () => {
  it('读失败画「读不出来」和重试，重试成功后画出内容', async () => {
    get.mockRejectedValueOnce(new Error('boom'))
    const { findByText, queryByText } = mount()

    const retry = await findByText('ratchet.state.retry')
    expect(queryByText('ratchet.prov.archived {"count":2}')).toBeNull()

    get.mockResolvedValueOnce(BOARD)
    await fireEvent.click(retry)

    expect(await findByText('ratchet.prov.archived {"count":2}')).toBeTruthy()
    await waitFor(() => expect(get).toHaveBeenCalledTimes(2))
  })

  it('读失败之后刷新成功，上一屏的错误不再盖着已经拿到的内容', async () => {
    get.mockRejectedValueOnce(new Error('boom'))
    const { findByText, getByText, getByLabelText, queryByText } = mount()

    expect(await findByText('ratchet.state.loadFailed')).toBeTruthy()

    refresh.mockResolvedValueOnce({
      ...BOARD,
      refresh: { repo: BOARD.repo, listed: 12, stored: 1, already_stored: 11, unreadable: 0, failed: 0, error: '' },
    })
    // 刷新是页头那枚图标按钮：字在 `aria-label` 上，没有一个文本节点可找。按**可访问名**
    // 找它 —— 这也正是读屏软件找它的方式。全后台页头的工具区都是这个形态
    // （`AdminSpacesPage` / `AdminMembersPage` 同款）。
    await fireEvent.click(getByLabelText('ratchet.action.refresh'))

    // 手上已经有整页数据了——「读不出来」那一支必须消失，否则屏幕上的内容被一
    // 句过期的错继续盖着。
    expect(await findByText('ratchet.prov.archived {"count":2}')).toBeTruthy()
    expect(queryByText('ratchet.state.loadFailed')).toBeNull()
    expect(getByText('ratchet.pull.done {"listed":12,"stored":1,"already":11}')).toBeTruthy()
  })

  it('刷新自己失败时说的是「这次没拉到」和原因，不画成读不出来', async () => {
    get.mockResolvedValueOnce(BOARD)
    refresh.mockResolvedValueOnce({
      ...BOARD,
      refresh: {
        repo: BOARD.repo,
        listed: 12,
        stored: 0,
        already_stored: 0,
        unreadable: 0,
        failed: 0,
        error: 'GitHub 403',
      },
    })
    const { findByText, findByLabelText, queryByText } = mount()

    await fireEvent.click(await findByLabelText('ratchet.action.refresh'))

    expect(await findByText('ratchet.pull.failed {"error":"GitHub 403"}')).toBeTruthy()
    expect(queryByText('ratchet.state.loadFailed')).toBeNull()
    // 拉不到不是这一页挂了：已经存下的点还在画。
    expect(queryByText('ratchet.prov.archived {"count":2}')).toBeTruthy()
  })

  it('交给视图的 failed 在刷新成功之后是 false —— 这一层的契约', async () => {
    get.mockRejectedValueOnce(new Error('boom'))
    const { container, getByText } = mirror()

    const failedProp = () => container.querySelector('[data-mirror="failed"]')?.textContent
    await waitFor(() => expect(failedProp()).toBe('true'))

    refresh.mockResolvedValueOnce({
      ...BOARD,
      refresh: { repo: BOARD.repo, listed: 1, stored: 1, already_stored: 0, unreadable: 0, failed: 0, error: '' },
    })
    await fireEvent.click(getByText('pull'))

    await waitFor(() => expect(failedProp()).toBe('false'))
  })

  it('刷新整个请求挂掉时说「请求本身失败了」，已经画好的内容留着', async () => {
    get.mockResolvedValueOnce(BOARD)
    refresh.mockRejectedValueOnce(new Error('network down'))
    const { findByText, findByLabelText, queryByText } = mount()

    await fireEvent.click(await findByLabelText('ratchet.action.refresh'))

    expect(await findByText('ratchet.pull.unreachable')).toBeTruthy()
    expect(queryByText('ratchet.state.loadFailed')).toBeNull()
    expect(queryByText('ratchet.prov.archived {"count":2}')).toBeTruthy()
  })
})
