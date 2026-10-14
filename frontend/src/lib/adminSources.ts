// 一条线路（模型名下的一条网关部署）在表单里的样子，以及它怎么变成请求体。
//
// 新建模型时的第一条线路、给已有模型加线路、改一条线路，三处收的是同一组字段，所以
// 草稿、校验与「草稿 → 请求体」都放在这里，表单组件只管画。校验与服务端
// （`backend/app/domain/agent/schemas.py`、`gateway_models.py`）同一套规则，写在界面上
// 是为了省一次往返，判据仍以服务端为准。

import type { GatewayProtocol, GatewaySourceFields } from '@/api/admin/gateway'
import type { SourceRow } from './adminModels'

import { apiBaseAllowed } from '@/utils/apiBase'

/** 表单里的协议：三种协议，加上「其他」—— 那时上游模型填完整的 LiteLLM 模型串。 */
export type ProtocolChoice = GatewayProtocol | 'other'

export const PROTOCOL_CHOICES: ProtocolChoice[] = ['anthropic', 'openai-chat', 'openai-responses', 'other']

/** 协议 → 词条。写成字面量表，不在模板里拼键：键要能被静态找到。 */
export const PROTOCOL_KEY: Record<ProtocolChoice, string> = {
  anthropic: 'models.source.protocol.anthropic',
  'openai-chat': 'models.source.protocol.openaiChat',
  'openai-responses': 'models.source.protocol.openaiResponses',
  other: 'models.source.protocol.other',
}

/** 优先级的取值范围（LiteLLM 的 `order`），与服务端相同。 */
export const ORDER_MIN = 1
export const ORDER_MAX = 100

export interface HeaderDraft {
  name: string
  value: string
}

/** 表单状态。数字都用字符串收（输入框给的就是字符串），提交时才解析 —— 空框和 0 是
 *  两回事：0 是「免费」，空是「没填」。 */
export interface SourceDraft {
  protocol: ProtocolChoice
  upstream: string
  apiBase: string
  apiKey: string
  order: string
  free: boolean
  prices: { input: string; output: string; cacheRead: string; cacheCreation: string }
  /** 编辑时请求头的值不回显，所以「改头」是一个显式动作：没动过就整份不发。 */
  headersTouched: boolean
  headers: HeaderDraft[]
  /** 编辑时这条线路现有的头名（只有名字）。 */
  existingHeaderNames: string[]
  sessionHeader: string
}

export function emptySourceDraft(order = ORDER_MIN): SourceDraft {
  return {
    protocol: 'anthropic',
    upstream: '',
    apiBase: '',
    apiKey: '',
    order: String(order),
    free: false,
    prices: { input: '', output: '', cacheRead: '', cacheCreation: '' },
    headersTouched: true,
    headers: [],
    existingHeaderNames: [],
    sessionHeader: '',
  }
}

/** 每 token → 每百万的输入框文本。0 显示成 0（免费线路的价就是 0，不是空）。 */
function perMillionText(v: number | null | undefined): string {
  if (v === null || v === undefined) return ''
  return String(Number((v * 1_000_000).toFixed(8)))
}

/** 编辑一条已有线路时的初值。凭据、请求头的值、上游地址的全文服务端都不下发，
 *  这三样留空，空着提交就是「不动」。 */
export function draftFromSource(source: SourceRow): SourceDraft {
  return {
    protocol: source.protocol ?? 'other',
    upstream: source.protocol ? source.upstream_model : source.upstream_raw,
    apiBase: '',
    apiKey: '',
    order: source.order === null ? '' : String(source.order),
    free: source.free,
    prices: {
      input: perMillionText(source.prices.input),
      output: perMillionText(source.prices.output),
      cacheRead: perMillionText(source.prices.cache_read),
      cacheCreation: perMillionText(source.prices.cache_creation),
    },
    headersTouched: false,
    headers: [],
    existingHeaderNames: [...source.header_names],
    sessionHeader: source.session_header ?? '',
  }
}

/** 每百万 → 每 token，空串给 null，不是数给 undefined（表单会把它判成不合法）。 */
function perToken(text: string): number | null | undefined {
  const v = text.trim()
  if (v === '') return null
  const n = Number(v)
  if (!Number.isFinite(n) || n < 0) return undefined
  return n / 1_000_000
}

const HEADER_NAME = /^[!#$%&'*+.^_`|~0-9A-Za-z-]{1,64}$/
// 认证与传输相关的头不许按线路写（同服务端的 `_RESERVED_HEADERS`）。
const RESERVED_HEADERS = new Set([
  'authorization',
  'proxy-authorization',
  'x-api-key',
  'api-key',
  'anthropic-api-key',
  'cookie',
  'host',
  'content-length',
  'transfer-encoding',
  'connection',
  'x-claude-code-session-id',
])
const HEADER_VALUE_MAX = 512
const CONTROL = /[\u0000-\u001f\u007f]/

/** 一个头名为什么不能用：`name`（字符不合法）/ `reserved`（认证或传输头）；能用给 null。 */
export function headerNameProblem(name: string): 'name' | 'reserved' | null {
  const n = name.trim()
  if (!HEADER_NAME.test(n)) return 'name'
  if (RESERVED_HEADERS.has(n.toLowerCase())) return 'reserved'
  return null
}

export function headerValueOk(value: string): boolean {
  return value.length <= HEADER_VALUE_MAX && !CONTROL.test(value)
}

export interface DraftProblems {
  upstream: boolean
  apiBase: boolean
  order: boolean
  prices: boolean
  /** 免费线路填了非零价，或没标免费却把输入 / 输出写成 0。 */
  free: boolean
  headers: boolean
  sessionHeader: boolean
}

export function draftProblems(draft: SourceDraft): DraftProblems {
  const order = Number(draft.order)
  const input = perToken(draft.prices.input)
  const output = perToken(draft.prices.output)
  const anyPriceInvalid = [draft.prices.input, draft.prices.output, draft.prices.cacheRead, draft.prices.cacheCreation]
    .map(perToken)
    .some((v) => v === undefined)
  const zero = (v: number | null | undefined) => v === 0
  const positiveOrEmpty = (v: number | null | undefined) => v === null || (v !== undefined && v > 0)
  const names = draft.headers.map((h) => h.name.trim().toLowerCase())
  return {
    upstream: draft.upstream.trim() === '',
    apiBase: !apiBaseAllowed(draft.apiBase),
    order: !(Number.isInteger(order) && order >= ORDER_MIN && order <= ORDER_MAX),
    prices: anyPriceInvalid,
    free: draft.free
      ? !((input === null || zero(input)) && (output === null || zero(output)))
      : !(positiveOrEmpty(input) && positiveOrEmpty(output)),
    headers:
      draft.headersTouched &&
      (draft.headers.some((h) => headerNameProblem(h.name) !== null || !headerValueOk(h.value)) ||
        new Set(names).size !== names.length),
    sessionHeader: draft.sessionHeader.trim() !== '' && headerNameProblem(draft.sessionHeader) !== null,
  }
}

export function draftValid(draft: SourceDraft): boolean {
  return !Object.values(draftProblems(draft)).some(Boolean)
}

/** 这条线路算不算有价：两向都 > 0，或者标了免费（免费线路的价由服务端写成 0）。
 *  上架开关的闸门用它 —— 上架的模型每条线路都得过这一关。 */
export function draftPriced(draft: SourceDraft): boolean {
  if (draft.free) return true
  const input = perToken(draft.prices.input)
  const output = perToken(draft.prices.output)
  return typeof input === 'number' && input > 0 && typeof output === 'number' && output > 0
}

/** 草稿 → 请求体。`mode = 'edit'` 时没填的凭据 / 地址、没动过的请求头都不发（不发 =
 *  不动），凭据还要显式声明 `api_key_unchanged`。免费线路不发输入输出价：服务端写 0。 */
export function draftToFields(draft: SourceDraft, mode: 'add' | 'edit'): GatewaySourceFields & { order?: number } {
  const out: GatewaySourceFields = {
    protocol: draft.protocol === 'other' ? null : draft.protocol,
    upstream_model: draft.upstream.trim(),
    order: Number(draft.order),
    free: draft.free,
    prices: {
      input: draft.free ? 0 : perToken(draft.prices.input) ?? null,
      output: draft.free ? 0 : perToken(draft.prices.output) ?? null,
      cache_read: perToken(draft.prices.cacheRead) ?? null,
      cache_creation: perToken(draft.prices.cacheCreation) ?? null,
    },
    session_header: draft.sessionHeader.trim(),
  }
  if (draft.apiBase.trim()) out.api_base = draft.apiBase.trim()
  if (draft.apiKey.trim()) out.api_key = draft.apiKey.trim()
  else if (mode === 'edit') out.api_key_unchanged = true
  if (draft.headersTouched) {
    out.upstream_headers = Object.fromEntries(draft.headers.map((h) => [h.name.trim(), h.value]))
  }
  return out
}
