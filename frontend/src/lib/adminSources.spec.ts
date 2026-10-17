/**
 * 一条线路的草稿怎么变成请求体、什么样的草稿不放行。
 *
 * 钉的都是不读实现也说得出的规则：编辑时没填的凭据与没动过的请求头不能被当成「清空」
 * 发出去；零价只属于免费线路；认证头与带换行的值不能按线路写；「其他」协议发的是
 * 完整模型串。
 */
import type { SourceRow } from './adminModels'

import { describe, expect, it } from 'vitest'

import {
  defaultOrder,
  draftFromSource,
  draftPriced,
  draftProblems,
  draftToFields,
  draftValid,
  emptySourceDraft,
  mixesFreeAndPaid,
  wouldLead,
} from './adminSources'

function source(over: Partial<SourceRow> = {}): SourceRow {
  return {
    source_id: 's-1',
    origin: 'runtime',
    blocked: false,
    order: 2,
    protocol: 'openai-chat',
    upstream_model: 'kimi-k3',
    upstream_raw: 'hosted_vllm/kimi-k3',
    host: 'opencode.ai',
    prices: { input: 1e-6, output: 4e-6 },
    free: false,
    priced: true,
    header_names: ['user-agent'],
    session_header: 'x-opencode-session',
    ...over,
  }
}

describe('线路草稿 · 编辑时「没填」是「别动」', () => {
  it('没填密钥：不发密钥，并声明沿用', () => {
    const fields = draftToFields(draftFromSource(source()), 'edit')
    expect(fields.api_key).toBeUndefined()
    expect(fields.api_key_unchanged).toBe(true)
    expect(fields.api_base).toBeUndefined()
  })

  it('没点「重新设置请求头」：请求头整份不发，现有的不会被清掉', () => {
    const fields = draftToFields(draftFromSource(source()), 'edit')
    expect(fields).not.toHaveProperty('upstream_headers')
  })

  it('点了重新设置、一行不留：发空表，也就是清空', () => {
    const draft = { ...draftFromSource(source()), headersTouched: true, headers: [] }
    expect(draftToFields(draft, 'edit').upstream_headers).toEqual({})
  })

  it('会话头沿用现值；清空它发的是空串，不是「别动」', () => {
    expect(draftToFields(draftFromSource(source()), 'edit').session_header).toBe('x-opencode-session')
    const cleared = { ...draftFromSource(source()), sessionHeader: '' }
    expect(draftToFields(cleared, 'edit').session_header).toBe('')
  })
})

describe('线路草稿 · 协议', () => {
  it('选了协议发的是上游自己的名字；「其他」发完整模型串', () => {
    const chat = { ...emptySourceDraft(), protocol: 'openai-chat' as const, upstream: 'glm-5.2' }
    expect(draftToFields(chat, 'add')).toMatchObject({ protocol: 'openai-chat', upstream_model: 'glm-5.2' })
    const other = draftFromSource(source({ protocol: null, upstream_model: 'deepseek/x', upstream_raw: 'deepseek/x' }))
    expect(other.protocol).toBe('other')
    expect(draftToFields(other, 'edit')).toMatchObject({ protocol: null, upstream_model: 'deepseek/x' })
  })
})

describe('线路草稿 · 免费与零价', () => {
  function priced(input: string, output: string, free = false) {
    return {
      ...emptySourceDraft(),
      upstream: 'm',
      free,
      prices: { input, output, cacheRead: '', cacheCreation: '' },
    }
  }

  it('免费线路发两个 0，算有价', () => {
    const draft = priced('', '', true)
    expect(draftValid(draft)).toBe(true)
    expect(draftPriced(draft)).toBe(true)
    expect(draftToFields(draft, 'add').prices).toMatchObject({ input: 0, output: 0 })
  })

  it('没标免费的 0 价不放行', () => {
    expect(draftProblems(priced('0', '2')).free).toBe(true)
    expect(draftProblems(priced('1', '0')).free).toBe(true)
  })

  it('两向都有价才算有价；只填一半不能上架', () => {
    expect(draftPriced(priced('1', '2'))).toBe(true)
    expect(draftPriced(priced('1', ''))).toBe(false)
  })
})

describe('线路草稿 · 请求头', () => {
  function withHeader(name: string, value: string) {
    return { ...emptySourceDraft(), upstream: 'm', headersTouched: true, headers: [{ name, value }] }
  }

  it.each([
    ['Authorization', 'Bearer x'],
    ['x-api-key', 'k'],
    ['user agent', 'x'],
    ['user-agent', 'a\r\nx-api-key: stolen'],
  ])('%s 不放行', (name, value) => {
    expect(draftValid(withHeader(name, value))).toBe(false)
  })

  it('普通的静态头放行，原样发出', () => {
    const draft = withHeader('user-agent', 'cheese-gateway/1.0')
    expect(draftValid(draft)).toBe(true)
    expect(draftToFields(draft, 'add').upstream_headers).toEqual({ 'user-agent': 'cheese-gateway/1.0' })
  })

  it('会话头名同一条规则', () => {
    expect(draftProblems({ ...emptySourceDraft(), sessionHeader: 'Authorization' }).sessionHeader).toBe(true)
    expect(draftProblems({ ...emptySourceDraft(), sessionHeader: 'x-opencode-session' }).sessionHeader).toBe(false)
  })
})

describe('线路草稿 · 优先级', () => {
  it.each(['0', '101', '1.5', ''])('%s 不放行', (order) => {
    expect(draftProblems({ ...emptySourceDraft(), upstream: 'm', order }).order).toBe(true)
  })
})

describe('线路草稿 · 新线路排在哪', () => {
  it('默认排在现有带优先级的线路之后', () => {
    expect(defaultOrder([source({ order: 1 }), source({ order: 3 })])).toBe(4)
  })

  it('排到最前、或旁边的线路都没写优先级，就是接走流量', () => {
    expect(wouldLead([source({ order: 2 })], 2)).toBe(true)
    expect(wouldLead([source({ order: 2 })], 3)).toBe(false)
    expect(wouldLead([source({ order: null })], 50)).toBe(true)
    // 整组都停着时谁也不接走谁。
    expect(wouldLead([source({ order: 2, blocked: true })], 1)).toBe(false)
  })

  it('免费与付费混用要说出来', () => {
    expect(mixesFreeAndPaid([source({ free: false })], true)).toBe(true)
    expect(mixesFreeAndPaid([source({ free: false })], false)).toBe(false)
  })
})
