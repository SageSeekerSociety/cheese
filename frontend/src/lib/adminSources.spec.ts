/**
 * 一条线路的草稿怎么变成请求体、什么样的草稿不放行。
 *
 * 钉的都是不读实现也说得出的规则：编辑时没填的凭据与没动过的请求头不能被当成「清空」
 * 发出去；两向都是 0 只属于免费线路；认证头与带换行的值不能按线路写；「其他」协议发的是
 * 完整模型串。
 */
import type { SourceRow } from './adminModels'

import { describe, expect, it } from 'vitest'

import {
  capProgress,
  defaultOrder,
  draftFromSource,
  draftPriced,
  draftProblems,
  draftToFields,
  draftValid,
  emptySourceDraft,
  fullestCap,
  mixesFreeAndPaid,
  priceChanges,
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
    spend_cap_usd: null,
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
      prices: { input, output, cacheRead: '', cacheCreation: '', cacheCreation1h: '' },
    }
  }

  it('免费线路发两个 0，算有价', () => {
    const draft = priced('', '', true)
    expect(draftValid(draft)).toBe(true)
    expect(draftPriced(draft)).toBe(true)
    expect(draftToFields(draft, 'add').prices).toMatchObject({ input: 0, output: 0 })
  })

  it('一向是 0 放行（语音转写的输出不收钱）；两向都是 0 要标免费', () => {
    expect(draftValid(priced('1', '0'))).toBe(true)
    expect(draftProblems(priced('0', '0')).free).toBe(true)
  })

  it('两向都 > 0 才能上架；只填一半、或一向是 0 不能', () => {
    expect(draftPriced(priced('1', '2'))).toBe(true)
    expect(draftPriced(priced('1', ''))).toBe(false)
    expect(draftPriced(priced('1', '0'))).toBe(false)
  })
})

describe('线路草稿 · 请求头', () => {
  function withHeader(name: string, value: string) {
    return {
      ...emptySourceDraft(),
      upstream: 'm',
      prices: { input: '1', output: '2', cacheRead: '', cacheCreation: '', cacheCreation1h: '' },
      headersTouched: true,
      headers: [{ name, value }],
    }
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

describe('线路草稿 · 消费上限', () => {
  it('没改上限：不发，同一上游的别的线路不被这次编辑碰到', () => {
    expect(draftToFields(draftFromSource(source({ spend_cap_usd: 2000 })), 'edit')).not.toHaveProperty('spend_cap_usd')
  })

  it('改了发新数，清空发 null', () => {
    const draft = draftFromSource(source({ spend_cap_usd: 2000 }))
    expect(draftToFields({ ...draft, spendCap: '3000' }, 'edit').spend_cap_usd).toBe(3000)
    expect(draftToFields({ ...draft, spendCap: '' }, 'edit').spend_cap_usd).toBeNull()
  })

  it('原来没有上限、现在填上', () => {
    const draft = draftFromSource(source())
    expect(draftToFields({ ...draft, spendCap: '500' }, 'edit').spend_cap_usd).toBe(500)
  })

  it.each(['0', '-1', 'abc'])('%s 不放行', (spendCap) => {
    expect(draftValid({ ...draftFromSource(source()), spendCap })).toBe(false)
  })

  it('加线路不发上限：服务端从同一上游的线路抄', () => {
    expect(draftToFields({ ...emptySourceDraft(), upstream: 'm', spendCap: '500' }, 'add')).not.toHaveProperty(
      'spend_cap_usd'
    )
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

describe('线路草稿 · 每条线路都要有价', () => {
  it('没标免费、输入或输出空着：不放行', () => {
    const draft = { ...emptySourceDraft(), upstream: 'm' }
    expect(draftProblems(draft).unpriced).toBe(true)
    expect(draftValid(draft)).toBe(false)
    expect(draftProblems({ ...draft, free: true }).unpriced).toBe(false)
  })
})

describe('线路草稿 · 改价', () => {
  it('列出改了的那几档，改前改后；没改的、没填的不列', () => {
    const before = { input: 2e-6, output: 1e-5, cache_read: 2e-7 }
    const changes = priceChanges(before, { input: 4e-6, output: 1e-5, cache_read: null, cache_creation_1h: 8e-6 })
    expect(changes).toEqual([
      { key: 'input', before: 2e-6, after: 4e-6 },
      { key: 'cache_creation_1h', before: null, after: 8e-6 },
    ])
  })

  it('价没动：什么都不列', () => {
    const prices = { input: 2e-6, output: 1e-5 }
    expect(priceChanges(prices, { ...prices })).toEqual([])
  })
})

describe('线路草稿 · 编辑时价没动就不发', () => {
  it('一条没价的老线路只改优先级：放行，请求里没有价也没有免费标记', () => {
    const draft = { ...draftFromSource(source({ prices: {}, priced: false })), order: '3' }
    expect(draftValid(draft)).toBe(true)
    const fields = draftToFields(draft, 'edit')
    expect(fields).not.toHaveProperty('prices')
    expect(fields).not.toHaveProperty('free')
  })

  it('改了一档价：整份价连同免费标记一起发', () => {
    const draft = draftFromSource(source())
    const fields = draftToFields({ ...draft, prices: { ...draft.prices, input: '3' } }, 'edit')
    expect(fields.prices?.input).toBeCloseTo(3e-6)
    expect(fields.free).toBe(false)
  })

  it('清空一档已有的价：不放行', () => {
    const draft = draftFromSource(source())
    expect(draftProblems({ ...draft, prices: { ...draft.prices, output: '' } }).cleared).toBe(true)
  })
})

describe('消费上限用到了哪里', () => {
  it('没设上限、或服务端没读到已用多少，就不给进度', () => {
    expect(capProgress(source({ spend_cap_usd: null, spend_cap_used_usd: null }))).toBeNull()
    expect(capProgress(source({ spend_cap_usd: 2000, spend_cap_used_usd: null }))).toBeNull()
  })

  it('八成起是警告，到顶是超出', () => {
    expect(capProgress(source({ spend_cap_usd: 2000, spend_cap_used_usd: 1000 }))).toEqual({
      used: 1000,
      cap: 2000,
      ratio: 0.5,
      level: 'ok',
    })
    expect(capProgress(source({ spend_cap_usd: 2000, spend_cap_used_usd: 1600 }))?.level).toBe('warn')
    expect(capProgress(source({ spend_cap_usd: 2000, spend_cap_used_usd: 2000 }))?.level).toBe('over')
  })

  it('列表上取用得最满的那条线路', () => {
    const fullest = fullestCap([
      source({ spend_cap_usd: 2000, spend_cap_used_usd: 500 }),
      source({ spend_cap_usd: 100, spend_cap_used_usd: 90 }),
      source({ spend_cap_usd: null }),
    ])
    expect(fullest?.ratio).toBeCloseTo(0.9)
    expect(fullestCap([source({ spend_cap_usd: null })])).toBeNull()
    expect(fullestCap(undefined)).toBeNull()
  })
})
