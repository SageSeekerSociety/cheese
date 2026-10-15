// 挂载应用之前要等齐的几件事（main.ts）。启动骨架（index.html）一直挂到这里放行：
// 等到的是这个人要看的那一页，而不是一副空外壳。

/** 第一页最长等多久。路由守卫里有网络请求（登录后落到哪个项目要先读项目清单），网
 *  卡着不动时也得挂载：那时画出来的是外壳，比一直停在骨架上强。会话恢复本身有自己的
 *  期限（lib/session.ts 的 TOKEN_REFRESH_BUDGET_MS），这个数比它长。 */
export const FIRST_PAGE_WAIT_MS = 15_000

/**
 * `<head>` 里的样式表都到了、用上了。首屏不再等它们：scripts/prerender.mjs 把它们改成
 * `<link rel="preload" as="style">`，下完由它自己的 onload 换回样式表，首屏用到的那部分
 * 内联在页面里。但应用挂上去时得有完整的样式，不然先画出一帧没样式的界面。加载失败的
 * 也算到了：它不会再来。等过 `ms` 也放行：挂载不能卡在它上面。
 */
export function stylesheetsLoaded(doc: Document = document, ms: number = FIRST_PAGE_WAIT_MS): Promise<void> {
  const links = [
    ...doc.querySelectorAll<HTMLLinkElement>(
      // 按落点提前下的那几张（`data-boot-preload`，index.html）是第一页自己的样式，由路由挂上，不在这里等。
      'link[rel="stylesheet"], link[rel="preload"][as="style"]:not([data-boot-preload])'
    ),
  ]
  let timer: ReturnType<typeof setTimeout> | undefined
  const late = new Promise<void>((resolve) => {
    timer = setTimeout(resolve, ms)
  })
  const all = Promise.all(links.map(applied)).then(() => undefined)
  return Promise.race([all, late]).finally(() => clearTimeout(timer))
}

// 预加载的那一种要过两道：先下完（load），onload 把它换成样式表，换过之后浏览器从缓存里
// 再解析一遍，用上了才有 `sheet`（又一次 load）。
async function applied(link: HTMLLinkElement): Promise<void> {
  for (let step = 0; step < 2; step++) {
    if (link.rel === 'stylesheet' && link.sheet) return
    const failed = await new Promise<boolean>((resolve) => {
      link.addEventListener('load', () => resolve(false), { once: true })
      link.addEventListener('error', () => resolve(true), { once: true })
    })
    if (failed) return
  }
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
