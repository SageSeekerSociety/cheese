import { beforeAll } from 'vitest'

// 每个用例文件在跑任何用例之前：把两份语言包都准备好。
//
// 为什么两份都要：英文那份不在入口里，用的时候现取（`src/i18n/index.ts`）。一大批用例
// 先 `setLocale('en')`、下一行就同步断言英文，等不了动态 import；还有一批在挂载前就用
// `t()` 取词。所以这里把它取回来。zh-CN 本来就跟着入口来，取它是同一份、当场返回。
//
// 为什么放在钩子里而不是这个文件的第一行：i18n 实例一建起来就把界面语言定下来（读
// localStorage，再看浏览器语言；happy-dom 报的是 en-US）。而用例文件里「在 import
// 之前写 localStorage」那个写法（`vi.hoisted`）钉的就是这个初始语言，它跑在 setup
// 文件之后——在这里先 import 一次 `@/i18n`，那把钉就钉了个空，按中文写夹具的用例会先
// 拿到英文词。`beforeAll` 比用例自己的钩子先跑、又晚于用例文件的模块加载，两边都不
// 挡住。
beforeAll(async () => {
  // 有的用例文件把整个 `@/i18n` 换成替身（只给一个 `t` 那种）：那种文件用自己那份假
  // i18n，不碰真语言包，替身里没有 `loadLocale` 就跳过。取包本身失败不吞：真的取不回
  // 来要看见。
  let load: ((locale: 'zh-CN' | 'en') => Promise<void>) | undefined
  try {
    const real = await import('@/i18n')
    if (typeof real.loadLocale === 'function') load = real.loadLocale
  } catch {
    return
  }
  if (!load) return
  await Promise.all([load('zh-CN'), load('en')])
})
