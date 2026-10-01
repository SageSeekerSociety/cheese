// 后端拒绝时说的那句话，按读者的语言显示。
//
// 错误体里 `error.message` 是那句中文（agent 和 CLI 照原样读它），`error.i18n` 是
// 那句话在词条 `apiError` 里的键和参数。浏览器按当前语言把键画成 `message`；这一
// 版不认识的键、或者没带键的错误，照服务器的原话显示。
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError, inviteExternalMember } from './api'

import { setLocale } from '@/i18n'

function refusal(error: Record<string, unknown>): Response {
  return {
    ok: false,
    status: 422,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => ({ code: 422, message: error.message, data: null, error: { name: 'ValidationError', ...error } }),
  } as unknown as Response
}

async function refusedWith(error: Record<string, unknown>): Promise<ApiError> {
  vi.stubGlobal('fetch', async () => refusal(error))
  return inviteExternalMember('p1', 'alice').then(
    () => {
      throw new Error('expected a refusal')
    },
    (e: unknown) => e as ApiError
  )
}

describe('服务器拒绝的那句话', () => {
  beforeEach(() => {
    localStorage.clear()
    setLocale('zh-CN')
  })
  afterEach(() => {
    vi.unstubAllGlobals()
    setLocale('zh-CN')
  })

  const pending = { message: '已经邀请过这个人，正在等他答复', i18n: { key: 'invitePending', params: {} } }

  it('英文界面显示英文', async () => {
    setLocale('en')
    const e = await refusedWith(pending)
    expect(e).toBeInstanceOf(ApiError)
    expect(e.message).toBe("This person has already been invited and hasn't answered yet")
  })

  it('中文界面显示原来那句中文', async () => {
    expect((await refusedWith(pending)).message).toBe('已经邀请过这个人，正在等他答复')
  })

  it('这一版不认识的键，照服务器的原话显示', async () => {
    setLocale('en')
    const e = await refusedWith({ message: '一句新加的话', i18n: { key: 'somethingNewer', params: {} } })
    expect(e.message).toBe('一句新加的话')
  })

  it('没带键的错误照原话显示', async () => {
    setLocale('en')
    expect((await refusedWith({ message: 'Project not found' })).message).toBe('Project not found')
  })
})
