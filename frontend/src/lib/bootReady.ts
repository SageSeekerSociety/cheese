// 挂载应用之前要等齐的几件事（main.ts）。启动骨架（index.html）一直挂到这里放行：
// 等到的是这个人要看的那一页，而不是一副空外壳。

/** 第一页最长等多久。路由守卫里有网络请求（登录后落到哪个项目要先读项目清单），网
 *  卡着不动时也得挂载：那时画出来的是外壳，比一直停在骨架上强。会话恢复本身有自己的
 *  期限（lib/session.ts 的 TOKEN_REFRESH_BUDGET_MS），这个数比它长。 */
export const FIRST_PAGE_WAIT_MS = 15_000

/**
 * `<head>` 里的样式表都到了、用上了。首屏不再等它们：scripts/prerender.mjs 把它们先按
 * 打印样式挂上（`media="print"`），下完由它自己的 onload 换成 all，首屏用到的那部分内联
 * 在页面里。但应用挂载时得有完整的样式，不然先画出一帧没样式的界面。加载失败的也算到了：
 * 它不会再来。等过 `ms` 也放行：挂载不能卡在它上面。
 */
export function stylesheetsLoaded(doc: Document = document, ms: number = FIRST_PAGE_WAIT_MS): Promise<void> {
  const links = [...doc.querySelectorAll<HTMLLinkElement>('link[rel="stylesheet"]')]
  let timer: ReturnType<typeof setTimeout> | undefined
  const late = new Promise<void>((resolve) => {
    timer = setTimeout(resolve, ms)
  })
  const all = Promise.all(links.map(applied)).then(() => undefined)
  return Promise.race([all, late]).finally(() => clearTimeout(timer))
}

// 下完就有 `sheet`；它自己的 onload 先于这里的监听跑（写在标签上的先登记），所以 load
// 之后 media 已经换过了。
async function applied(link: HTMLLinkElement): Promise<void> {
  if (link.sheet && link.media !== 'print') return
  await new Promise<void>((resolve) => {
    link.addEventListener('load', () => resolve(), { once: true })
    link.addEventListener('error', () => resolve(), { once: true })
  })
}

/**
 * 第一页定下来了：会话恢复有了结果（手里有一份本地会话时要先续签，见
 * services/account.ts），第一次导航也走完了（落到哪一页、那一页的代码块都已到手）。
 * 两样都不会让它失败；超过 `ms` 也放行。
 */
export function firstPageReady(
  sessionRestored: Promise<unknown>,
  routerReady: Promise<unknown>,
  ms: number = FIRST_PAGE_WAIT_MS
): Promise<void> {
  let timer: ReturnType<typeof setTimeout> | undefined
  const late = new Promise<void>((resolve) => {
    timer = setTimeout(resolve, ms)
  })
  const settled = Promise.allSettled([sessionRestored, routerReady]).then(() => undefined)
  return Promise.race([settled, late]).finally(() => clearTimeout(timer))
}
