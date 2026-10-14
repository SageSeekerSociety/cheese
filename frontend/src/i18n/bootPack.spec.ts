// 挂载等首屏那份语言包的那一下要有上限：请求卡在网络上（既不成功也不失败）时也得
// 落定，不然启动骨架一直挂着（`main.ts`、这个模块的 `awaitBootPack`）。取不回来和
// 等过头都退回入口那一门，界面里不会一半中文一半英文；等过头的那一种，包之后到了就
// 补上——他浏览器要的就是它，除非这期间有人自己选了别的语言。
import { beforeEach, describe, expect, it, vi } from 'vitest'

import i18n, { awaitBootPack, setLocale, storedLocale } from './index'

const locale = () => i18n.global.locale.value

beforeEach(() => {
  localStorage.clear()
  // 英文那一门原来画在界面上，退回才看得出来。
  setLocale('en')
})

describe('the first-screen pack has a deadline', () => {
  it('settles on the entry locale when the pack never lands', async () => {
    await awaitBootPack(new Promise(() => {}), 20)
    expect(locale()).toBe('zh-CN')
    // 退回不是谁选的：浏览器里那份选择不动，下次打开还照它去取。
    expect(storedLocale()).toBe('en')
  })

  it('settles on the entry locale when the pack fails', async () => {
    await awaitBootPack(Promise.reject(new Error('offline')), 20)
    expect(locale()).toBe('zh-CN')
  })

  it('leaves the locale alone when the pack lands, and drops the deadline', async () => {
    await awaitBootPack(Promise.resolve(), 20)
    await new Promise((done) => setTimeout(done, 30))
    // 到点那个定时器已经清掉：它不会再回过头来把界面退回入口那一门。
    expect(locale()).toBe('en')
  })

  it('catches up when the pack lands after the deadline', async () => {
    let land: (() => void) | undefined
    const pack = new Promise<void>((resolve) => {
      land = resolve
    })
    await awaitBootPack(pack, 20)
    // 期限先到：界面退回入口那一门，挂载不等了。
    expect(locale()).toBe('zh-CN')

    land?.()
    // 包到了就用上：他浏览器要的就是它，不然这一趟一直是中文。
    await vi.waitFor(() => expect(locale()).toBe('en'))
  })

  it('does not take the language back from someone who picked one meanwhile', async () => {
    let land: (() => void) | undefined
    const pack = new Promise<void>((resolve) => {
      land = resolve
    })
    await awaitBootPack(pack, 20)
    // 等包的这段时间里，他从退回的界面自己选了一门。
    setLocale('zh-CN')

    land?.()
    await new Promise((done) => setTimeout(done, 30))
    expect(locale()).toBe('zh-CN')
  })
})
