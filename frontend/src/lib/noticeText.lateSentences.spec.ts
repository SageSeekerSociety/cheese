// 后端说的报错和系统消息是页面画出来之后才取的那一块（`loadPlatformSentences`）。在它
// 到之前被拒的那一下，读者看到的不能是裸键，英文读者也不该看到中文。
import { beforeEach, describe, expect, it, vi } from 'vitest'

const refused = { error: { i18n: { key: 'accessDenied' } } }

/** 刚打开的一页：语言包按首屏那样到位，后端那两类话还没取。 */
async function freshPage() {
  vi.resetModules()
  const i18n = await import('@/i18n')
  const { refusalText } = await import('./noticeText')
  await Promise.all([i18n.loadLocale('zh-CN'), i18n.loadLocale('en')])
  return { ...i18n, refusalText }
}

beforeEach(() => {
  localStorage.clear()
})

describe('a refusal said before the backend sentences arrive', () => {
  it('a Chinese reader reads the sentence the server sent with it', async () => {
    const { setLocale, refusalText } = await freshPage()
    setLocale('zh-CN')
    expect(refusalText(refused, '无权访问')).toBe('无权访问')
  })

  it('an English reader reads it in English', async () => {
    const { setLocale, refusalText } = await freshPage()
    setLocale('en')
    expect(refusalText(refused, '无权访问')).toBe('Access denied')
  })

  it('once they arrive, the catalog says it in the reader’s language', async () => {
    const { setLocale, refusalText, loadPlatformSentences } = await freshPage()
    await loadPlatformSentences()
    setLocale('zh-CN')
    expect(refusalText(refused, 'server words')).toBe('无权访问')
  })
})
