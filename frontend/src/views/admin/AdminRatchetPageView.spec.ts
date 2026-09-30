/**
 * 「棘轮」这一屏的状态机 —— 它存在的理由里有一半是**把两件长得像的事分开画**。
 * 这一组钉住其中两组，两组都对应真实后端的形状（不是编的）：
 *
 * 1. **读失败**（props `failed`，手上还没有 board）和**还没有采集**（`points === 0`）：
 *    前者是这次请求挂了，后者是归档本来就空——接口一挂被画成「一切正常」是最坏的一种。
 * 2. **归档里有采集、却一条测量都没有**（`points > 0` 而 `areas` 为空）和**一条都没归档**：
 *    真实契约里一次失败的采集会入档成一条洞，读出来正是 `points=1`、`total_stored=1`、
 *    `areas=[]`、`collection="failed"`、`reason` 有值（`backend/app/domain/ratchet/board.py`
 *    的 `build_board`）。把它当成「一条都没归档」，会把失败历史和采集来源一起藏起来，
 *    而那正是唯一能解释「为什么空着」的东西。
 *
 * 3. **最近一次采集失败**时那一行说的话，和采集失败在归档里的真实形状对得上：失败的那
 *    一行没有检查记录，于是每一道检查在这个点上都是洞——「现在」那一格是「还没测到」，
 *    表里一个数都没有。所以那一行说的是「下面画的就是这一次的答案」，不是「上面这些数
 *    来自更早的一次」（后者是这一屏原先写的，现在钉住它不回来）。
 */
import type { RatchetBoard, RatchetCheck, RatchetPoint } from '@/views/admin/ratchetApi'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { render } from '@testing-library/vue'
import { describe, expect, it, vi } from 'vitest'

// 词条本身对不对由 `i18n/catalog.spec.ts` 管；这一组问的是走了哪一档。带参数的键把
// 参数一起拼进去，于是「原因有没有原样带出来」「数是几」都在断言里看得见。
vi.mock('vue-i18n', async () => {
  const actual = await vi.importActual<typeof import('vue-i18n')>('vue-i18n')
  return {
    ...actual,
    useI18n: () => ({
      t: (key: string, params?: Record<string, unknown>) => (params ? `${key} ${JSON.stringify(params)}` : key),
    }),
  }
})

import AdminRatchetPageView from './AdminRatchetPageView.vue'

/** 一道检查的两个点，都是 `pass`、都量到 3。
 *
 *  `lastIsHole` 把最新那个点换成洞——一次失败的采集在归档里没有检查记录，`_point_for`
 *  于是给每一道检查补一个洞（`status="not_collected"`、`actual=null`、
 *  `rule_fingerprint=null`），`_direction` 也因为没有两个连着量到的点而报 `unknown`。
 *  这不是编的形状：`collection="failed"` 的行就是这么来的（采集器写 `collection:
 *  "failed"` 那份快照时 `checks` 是空的），所以这一组的板子也这么填。 */
function check(lastIsHole = false): RatchetCheck {
  const points: RatchetPoint[] = [
    {
      commit: 'aaaaaaa1',
      collected_at: '2026-09-30T10:00:00+00:00',
      run_url: 'https://example.test/run/1',
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
    {
      commit: 'bbbbbbb2',
      collected_at: '2026-09-30T11:00:00+00:00',
      run_url: 'https://example.test/run/2',
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
  ]
  if (lastIsHole) {
    points[1] = {
      ...points[1],
      collection: 'failed',
      status: 'not_collected',
      actual: null,
      frozen: null,
      rule_fingerprint: null,
      reason: '这次采集里没有这道检查',
    }
  }
  const last = points[points.length - 1]
  return {
    id: 'fe-boundary',
    area: 'boundaries',
    better: 'down',
    direction: lastIsHole ? 'unknown' : 'flat',
    status: last.status,
    actual: last.actual,
    frozen: last.frozen,
    stale: [],
    stale_count: null,
    rule_fingerprint: last.rule_fingerprint,
    points,
  }
}

function board(patch: Partial<RatchetBoard>): RatchetBoard {
  return {
    repo: 'SageSeekerSociety/cheese',
    generated_at: '2026-09-30T13:00:00Z',
    deployed_commit: 'dc069a4aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
    collected_commit: null,
    collected_at: null,
    run_url: null,
    collection: null,
    points: 0,
    total_stored: 0,
    collections: [],
    areas: [],
    ...patch,
  }
}

/** 一次失败的采集在归档里的样子 —— 数来自真实后端对 `.failed.txt` 工件的计算。 */
const FAILED_ARCHIVE = board({
  collected_commit: 'aaed1c00000000000000000000000000000000000',
  collected_at: '2026-09-29T09:00:00+00:00',
  run_url: 'https://example.test/run/aaed',
  collection: 'failed',
  points: 1,
  total_stored: 1,
  collections: [
    {
      commit: 'aaed1c00000000000000000000000000000000000',
      collected_at: '2026-09-29T09:00:00+00:00',
      run_url: 'https://example.test/run/aaed',
      collection: 'failed',
      reason: 'no interpreter (exit 127)',
    },
  ],
  areas: [],
})

function mount(props: Record<string, unknown>) {
  return render(AdminRatchetPageView, {
    props,
    global: { plugins: [createVuetify({ components, directives })] },
  })
}

describe('棘轮页的画面', () => {
  it('手上已经有归档时，上一屏的「读失败」不再遮住内容', () => {
    const live = board({
      collection: 'ok',
      points: 2,
      total_stored: 2,
      collections: [
        { commit: 'aaaaaaa1', collected_at: null, run_url: '', collection: 'ok', reason: null },
        { commit: 'bbbbbbb2', collected_at: null, run_url: '', collection: 'ok', reason: null },
      ],
      areas: [{ area: 'boundaries', checks: [check()] }],
    })
    const { getByText, queryByText } = mount({ board: live, failed: true })

    expect(getByText('ratchet.prov.archived {"count":2}')).toBeTruthy()
    expect(queryByText('ratchet.state.loadFailed')).toBeNull()
  })

  it('还没有 board 时，读失败画「读不出来」和重试，不画成「还没有采集」', () => {
    const { getByText, queryByText } = mount({ failed: true })

    expect(getByText('ratchet.state.loadFailed')).toBeTruthy()
    expect(getByText('ratchet.state.retry')).toBeTruthy()
    expect(queryByText('ratchet.state.empty')).toBeNull()
  })

  it('一条都没归档时画「还没有采集」，不摆来源行', () => {
    const { getByText, queryByText } = mount({ board: board({}) })

    expect(getByText('ratchet.state.empty')).toBeTruthy()
    expect(getByText('ratchet.state.emptyAction')).toBeTruthy()
    expect(queryByText(/ratchet\.prov\.archived/)).toBeNull()
  })

  it('归档里只有失败的采集时，说清「没量到」并带上采集自己报的原因，而不是「还没归档」', () => {
    const { getByText, queryByText } = mount({ board: FAILED_ARCHIVE })

    // 这一档和「一条都没归档」是两句不同的话——后者会把失败历史藏起来。
    expect(queryByText('ratchet.state.empty')).toBeNull()
    expect(
      getByText('ratchet.state.noMeasurementsFailed {"count":1,"reason":"no interpreter (exit 127)"}')
    ).toBeTruthy()
    // 来源行照旧：量的是哪个提交、什么时候、CI 上哪一次 run——没有它，这一屏的
    // 「空」就只是「某个时候的某个东西」，读者没法自己去核。
    expect(getByText('ratchet.prov.archived {"count":1}')).toBeTruthy()
    expect(getByText('aaed1c0000')).toBeTruthy()
    expect(getByText('2026-09-29 09:00Z')).toBeTruthy()
    expect(getByText('ratchet.prov.run')).toBeTruthy()
  })

  it('没有可用测量时不说「上面这些数来自更早的一次」——上面根本没有数', () => {
    const { queryByText } = mount({ board: FAILED_ARCHIVE })
    expect(queryByText('ratchet.prov.collectionFailed')).toBeNull()
  })

  it('最近一次采集失败、下面还有表时，那一行说的是这一次，并带上采集自己报的原因', () => {
    const live = board({
      collection: 'failed',
      points: 2,
      total_stored: 2,
      collections: [
        { commit: 'aaaaaaa1', collected_at: null, run_url: '', collection: 'ok', reason: null },
        {
          commit: 'bbbbbbb2',
          collected_at: null,
          run_url: '',
          collection: 'failed',
          reason: 'no interpreter (exit 127)',
        },
      ],
      areas: [{ area: 'boundaries', checks: [check(true)] }],
    })
    const { getByText, queryByText } = mount({ board: live })

    expect(getByText('ratchet.prov.collectionFailed {"reason":"no interpreter (exit 127)"}')).toBeTruthy()
    expect(getByText('ratchet.prov.archived {"count":2}')).toBeTruthy()
    // 表里一个数都没有——失败那一次在归档里没有检查记录，每一格都是洞。所以那一行
    // 不能说「上面这些数来自更早的一次」：上面根本没有数。
    expect(getByText('ratchet.value.notCollected')).toBeTruthy()
    expect(queryByText('3')).toBeNull()
  })

  it('归档里有采集、但没有原因时只说「没有可用测量」，不替采集编一个理由', () => {
    const noReason = board({
      collection: 'ok',
      points: 1,
      total_stored: 1,
      collections: [{ commit: 'ccccccc3', collected_at: null, run_url: '', collection: 'ok', reason: null }],
      areas: [],
    })
    const { getByText, queryByText } = mount({ board: noReason })

    expect(getByText('ratchet.state.noMeasurementsPlain {"count":1}')).toBeTruthy()
    expect(queryByText(/noMeasurementsFailed/)).toBeNull()
  })
})
