// 动效档（正常 / 慢放 ×4 / 关）怎么落到产品自己的时长 token 上。
//
// 要紧的是**不另写一套时长**。慢放只改一个倍数，改的是产品在 `:root` 里声明的那几条
// `--dur-*`（src/style.css），这个文件里一个毫秒数都不写死：产品哪天把 `--dur-base`
// 从 220ms 调成 200ms，预览站的慢放跟着变成 800ms，两边不会各有一套会漂移的数字。
//
// 名字也是从样式表里读出来的，不是写死的清单：产品新加一条 `--dur-xxx`，慢放自动
// 带上它。读不到样式表时（测试环境、还没加载完）什么都不改，绝不猜一个默认值。

export type MotionMode = 'normal' | 'slow' | 'off'

/** 倍数。关掉不是删过渡——过渡还在，只是不花时间，终态和正常档一模一样。 */
export const MOTION_FACTOR: Record<MotionMode, number> = { normal: 1, slow: 4, off: 0 }

// 档位上的倍数从 `MOTION_FACTOR` 拼出来，不在这里再写一遍数：倍数改了，按钮上的字
// 和那句说明一起改，不会出现「按钮写着 ×4、实际乘了 2」。改倍数时另有两处跟着改——
// `demoMotion.spec.ts` 和 `CatalogDetailPage.spec.ts` 里按 label 找按钮的那两行。
export const MOTION_MODES: ReadonlyArray<{ id: MotionMode; label: string }> = [
  { id: 'normal', label: '正常' },
  { id: 'slow', label: `慢放 ×${MOTION_FACTOR.slow}` },
  { id: 'off', label: '关' },
]

const DURATION_NAME = /^--dur-/

/**
 * 产品自己那四条时长 token 的名字，照 `src/style.css` 的 `:root` 抄。
 *
 * 慢放改的就是它们（这是这个文件存在的理由）；动效台给每一格标「用的哪条 token」时
 * 也要拿它来核对 —— 两边都从这一份读，不各写一份名字。token 改名时这份要跟着改，
 * 忘了 `demoTransitions.spec.ts` 会把名字和 `style.css` 对一遍，对不上就红。
 */
export const DURATION_TOKENS = ['--dur-press', '--dur-quick', '--dur-base', '--dur-slow']

/**
 * 每条 token 的原值，见过一次就记下来。
 *
 * 必须记：第二次从文档上读到的是自己写上去的放大值，拿来再乘一遍会越乘越大。名字倒是
 * 每次现读（见 `durationNames`），所以产品新加一条 `--dur-xxx`，慢放自动带上它。
 */
const originals = new Map<string, number>()

function collect(rules: CSSRuleList, into: Set<string>): void {
  for (let i = 0; i < rules.length; i += 1) {
    const rule = rules[i] as CSSStyleRule & { cssRules?: CSSRuleList }
    const style: CSSStyleDeclaration | undefined = rule.style
    if (style) {
      for (let j = 0; j < style.length; j += 1) {
        const name = style.item(j)
        if (name && DURATION_NAME.test(name)) into.add(name)
      }
    }
    if (rule.cssRules) collect(rule.cssRules, into)
  }
}

/** 产品声明了哪几条时长 token。跨域的样式表读不到，跳过就是。 */
function durationNames(): string[] {
  if (typeof document === 'undefined') return []
  const names = new Set<string>()
  for (const sheet of Array.from(document.styleSheets)) {
    try {
      if (sheet.cssRules) collect(sheet.cssRules, names)
    } catch {
      // 读不到就少一条 token，不影响别的。
    }
  }
  return [...names]
}

/** 这一条 token 声明的是多少毫秒。不是时长的（算不出来、或写的是 var()）返回 null。 */
function original(name: string): number | null {
  const known = originals.get(name)
  if (known !== undefined) return known
  if (typeof document === 'undefined') return null
  const ms = Number.parseFloat(getComputedStyle(document.documentElement).getPropertyValue(name))
  if (!Number.isFinite(ms)) return null
  originals.set(name, ms)
  return ms
}

/**
 * 把这一档写进 `root` 的样式上（子元素继承）。
 *
 * 给 `document.documentElement` 就对整篇生效；给一块舞台就只影响它里面。同一个元素
 * 反复调用是安全的：原值只读一次，每次都从原值重算。
 */
export function applyMotion(root: HTMLElement, mode: MotionMode): void {
  const factor = MOTION_FACTOR[mode]
  for (const name of durationNames()) {
    const ms = original(name)
    if (ms === null) continue
    root.style.setProperty(name, `${ms * factor}ms`)
  }
  root.dataset.motion = mode
}

/**
 * 系统开了「减少动态效果」时，动效开关默认落在「关」。
 *
 * 读不到这个偏好（老浏览器、没有 matchMedia 的环境）时按没开算——这是「默认不动」
 * 的那一侧：真开着减少动态的人不会因为读不到而看到满屏动效。
 */
export function prefersReducedMotion(): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false
  try {
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches
  } catch {
    return false
  }
}
