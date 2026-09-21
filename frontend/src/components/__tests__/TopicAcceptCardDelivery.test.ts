/** 卡面上写着这次交付的是什么 (#1085 结论三/五)。
 *
 * 后端从一开始就把这两样随卡下发——这次更新的是哪一项产物、这一版交出去的是什么
 * ——而界面上一直没有画出来：人在批准一次交付，却看不见批的是哪一份。所以这里断言
 * 的是「点采纳之前读得到」，而不是「字段传下来了」：
 *
 *   1. 交出去一份文件时，文件名在卡上，而且当场拿得走（快照在递卡那一刻就落好
 *      了，所以不必等采纳）；
 *   2. 交出去一个地址时，地址本身是可点的；
 *   3. 交出去一次合并时没有可拿的东西，那一格就只写产物和版本；
 *   4. 产物清单落地之前递的那些卡两样都没有，整块不出现，框子照旧。
 */
import type { AcceptCard, MergeStateInfo } from '../../cx_types'

import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fireEvent, render } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const getAcceptCards = vi.fn()
const downloadFile = vi.fn()

vi.mock('../../api', async () => {
  const actual = await vi.importActual<typeof import('../../api')>('../../api')
  return {
    ...actual,
    getAcceptCards: (...a: unknown[]) => getAcceptCards(...a),
    getPrChecks: vi.fn().mockResolvedValue({ available: false }),
    downloadFile: (...a: unknown[]) => downloadFile(...a),
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

async function mountWith(one: AcceptCard) {
  getAcceptCards.mockResolvedValue({ data: [one], has_more: false })
  const vuetify = createVuetify({ components, directives })
  const utils = render(TopicAcceptCard, {
    props: { topicId: 't1', topicStatus: 'active' },
    global: { plugins: [vuetify] },
  })
  await flush()
  return utils
}

function button(container: Element, label: string): HTMLButtonElement | undefined {
  return Array.from(container.querySelectorAll('button')).find((b) => b.textContent?.includes(label)) as
    | HTMLButtonElement
    | undefined
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  downloadFile.mockResolvedValue(undefined)
})

describe('这次交付的是什么，写在采纳按钮上方', () => {
  it('交一份文件：文件名在卡上，当场拿得走', async () => {
    const { container } = await mountWith(card({}))

    const text = container.textContent ?? ''
    expect(text).toContain('这次交付')
    // 第 4 版是这张卡自己那一版，不是它前面那一版：人定的是「这一版要不要成为
    // 当前版本」。这个数由后端按卡的状态算好。
    expect(text).toContain('结题报告')
    expect(text).toContain('第 4 版')
    expect(text).toContain('结题报告.docx')

    await fireEvent.click(button(container, '下载')!)
    await flush()

    expect(downloadFile).toHaveBeenCalledTimes(1)
    const [url, filename] = downloadFile.mock.calls[0]
    expect(String(url)).toContain('/accept-cards/card-1/deliverable')
    expect(filename).toBe('结题报告.docx')
  })

  it('交一个地址：地址本身可点', async () => {
    const { container } = await mountWith(
      card({
        artifact: { id: 'a2', name: '项目官网', version: 2 },
        deliverable: { kind: 'link', filename: null, url: 'https://site.example/report' },
      })
    )

    const link = Array.from(container.querySelectorAll('a')).find((a) =>
      a.getAttribute('href')?.includes('site.example')
    )
    expect(link).toBeTruthy()
    expect(container.textContent).toContain('项目官网')
    expect(button(container, '下载')).toBeUndefined()
  })

  it('交一次合并：没有可拿的东西，只写产物和版本', async () => {
    const { container } = await mountWith(
      card({
        artifact: { id: 'a3', name: '代码仓库', version: 7 },
        deliverable: { kind: 'merge', filename: null, url: null },
      })
    )

    expect(container.textContent).toContain('代码仓库')
    expect(container.textContent).toContain('第 7 版')
    expect(button(container, '下载')).toBeUndefined()
  })

  it('两样都没有的旧卡：这一格不出现，框子照旧', async () => {
    const { container } = await mountWith(card({ artifact: null, deliverable: null }))

    expect(container.textContent).not.toContain('这次交付')
    // 框子本身没受影响：采纳还在，提交标题还在。
    expect(button(container, '采纳')).toBeTruthy()
    expect(container.textContent).toContain('chore: do a thing')
  })

  it('拿不到那一份时说出原因，卡不变成一条错误', async () => {
    downloadFile.mockRejectedValue(new Error('这一版没有留存文件'))
    const { container } = await mountWith(card({}))

    await fireEvent.click(button(container, '下载')!)
    await flush()

    const alert = container.querySelector('[role="alert"]')
    expect(alert?.textContent).toContain('这一版没有留存文件')
    expect(button(container, '采纳')).toBeTruthy()
  })
})
