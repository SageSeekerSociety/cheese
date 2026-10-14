// 英文那份语言包在入口之外，谁要谁取（见 index.ts 的 `catalogs`）。这里管的是「还没
// 到」：还在路上时不能先换过去（那一下会画出一片裸键），回不来时不能留下半生不熟的
// 界面（一半的词是这门语言，另一半按 `locale` 走：日期、vuetify）。
//
// 造法：这份文件里 en 的取包由一个闸门控制——默认直接失败，用例要它放行时才放行。
// 闸门挂在替身模块的 `default` 上：`loadLocale` 取的就是 `m.default`，这里失败就等于
// 这次取包失败；写成 getter，每次取包都拿一个新的 promise，才谈得上「这一次失败、下一
// 次放行」。这份替身也会被 `setup-i18n.ts` 的预取拿到，所以下面把导出的 `loadLocale`
// 包一层替它吞掉失败（真的 reject 会让这份文件里所有用例跟着红）。
import { afterEach, describe, expect, it, vi } from 'vitest'

const gate = vi.hoisted(() => {
  const held: Array<() => void> = []
  const gate = {
    failing: true,
    wait<T>(value: T): Promise<T> {
      if (gate.failing) return Promise.reject(new Error('offline'))
      return new Promise<T>((resolve) => held.push(() => resolve(value)))
    },
    release() {
      for (const settle of held.splice(0)) settle()
    },
  }
  return gate
})

vi.mock('./messages/en', async (importOriginal) => {
  const actual = await importOriginal<{ default: unknown }>()
  return {
    get default() {
      return gate.wait(actual.default)
    },
  }
})

// setup 的预取走导出的那个 `loadLocale`，这里的包装替它吞掉失败；`chooseLocale` 用的是
// 模块内部那一个，照样落在闸门上。
vi.mock('./index', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./index')>()
  return {
    ...actual,
    async loadLocale(locale: 'zh-CN' | 'en') {
      await actual.loadLocale(locale).catch(() => {})
    },
  }
})

import i18n, { chooseLocale, localeReady, setLocale, storedLocale, unattendedPackIsLoading } from './index'

afterEach(() => {
  localStorage.removeItem('cheese:locale')
})

describe('a catalog that never arrives', () => {
  it('settles on the locale the entry carries', async () => {
    await localeReady
    expect(i18n.global.locale.value).toBe('zh-CN')
    // 这不是谁选的，偏好不动：下次打开还照浏览器语言去取。
    expect(storedLocale()).toBeNull()
    // 取不回来也落定，`services/staleBuild.ts` 那边照旧管缺块。
    expect(unattendedPackIsLoading()).toBe(false)
  })

  it('changes nothing when the visitor picks that language', async () => {
    setLocale('zh-CN')
    await expect(chooseLocale('en')).resolves.toBeUndefined()
    expect(i18n.global.locale.value).toBe('zh-CN')
    expect(storedLocale()).toBe('zh-CN')
  })
})

describe('a catalog still on its way', () => {
  it('switches only once it is here', async () => {
    setLocale('zh-CN')
    gate.failing = false

    const picking = chooseLocale('en')
    // 让出足够多的机会：谁要是没等包就先换了语言，这里会看见。
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(i18n.global.locale.value).toBe('zh-CN')
    expect(storedLocale()).toBe('zh-CN')

    gate.release()
    await picking
    expect(i18n.global.locale.value).toBe('en')
    expect(storedLocale()).toBe('en')
  })
})
