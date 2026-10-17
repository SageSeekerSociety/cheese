// 换语言要等语言包（见 index.ts 的 `chooseLocale`）。等的这段时间里，点的人还能再点
// 一次——点回当前显示的那一门也算（`SegmentedControl.vue` 点已经选中的那一段照样发事
// 件）。先出发的那一次回来时不能再落地，否则它会把后点的那一下盖回去：界面、本机偏
// 好，还有告诉账号的那一条，就都记成先前那一门了。
//
// 单独一份文件：这份用例要的是「还在路上」的那一份包，而 en 一旦取回来就记在模块里
// （`registered`），同一个文件里后面的用例再也摆不出这个局面。造法和 lazyCatalog.spec.ts
// 一样——en 的取包由一个闸门控制，用例要它放行时才放行；闸门挂在替身模块的 `default`
// 上（`loadLocale` 取的就是它），写成 getter 才每次取包都拿一个新的 promise。
import { afterEach, describe, expect, it, vi } from 'vitest'

const gate = vi.hoisted(() => {
  const held: Array<() => void> = []
  const gate = {
    // 一开始就失败：挂载前那次取包（`localeReady`）和 setup 的预取当场就完事，不会把
    // 钩子挂在闸门上。用例要摆「还在路上」时才放开。
    failing: true,
    held: () => held.length,
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

// setup 的预取走导出的那个 `loadLocale`，这里的包装替它吞掉失败（真的 reject 会让这份
// 文件跟着红）；`chooseLocale` 用的是模块内部那一个，照样落在闸门上。
vi.mock('./index', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./index')>()
  return {
    ...actual,
    async loadLocale(locale: 'zh-CN' | 'en') {
      await actual.loadLocale(locale).catch(() => {})
    },
  }
})

import i18n, { chooseLocale, onLocaleChosen, setLocale, storedLocale } from './index'

afterEach(() => {
  localStorage.removeItem('cheese:locale')
})

describe('a language pack still on its way', () => {
  it('does not land over the language picked after it', async () => {
    const told: Array<string> = []
    onLocaleChosen((locale) => told.push(locale))
    setLocale('zh-CN')
    gate.failing = false

    // 点英文：那一份包还挂在闸门上。等它真的挂上去，才是「路上那次没回来」的样子。
    const pickingEnglish = chooseLocale('en')
    await vi.waitFor(() => expect(gate.held()).toBe(1), { timeout: 10_000 })

    // 这时候点回中文。中文那份包在手上，当场就能落地。
    await chooseLocale('zh-CN')
    expect(i18n.global.locale.value).toBe('zh-CN')

    gate.release()
    await pickingEnglish
    // 后点的那一下说了算：界面、本机偏好，还有告诉账号的那一条。
    expect(i18n.global.locale.value).toBe('zh-CN')
    expect(storedLocale()).toBe('zh-CN')
    expect(told).toEqual(['zh-CN'])
  })
})
