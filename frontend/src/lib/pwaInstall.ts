/**
 * 安装到设备：浏览器什么时候允许我们问，以及现在算不算已经装了。
 *
 * `beforeinstallprompt` 是 **Chromium 独有的**（非标准，但没被废弃），而且它
 * **在页面很早就触发**——早到用户还没打开设置页。所以监听必须挂在应用启动时
 * （main.ts 的 watchInstallPrompt()），事件存成模块级的单例；等到「安装到手机」
 * 那一页出现时，事件早就在这里等着了。这也是它必须是一个不带组件的模块、而不是
 * 一个 composable 的原因：composable 的 ref 跟着组件生死，挂载时事件已经过去了。
 *
 * 这个事件实例**只能用一次**：`prompt()` 之后它就算用掉了，再调一次不会弹窗。
 * 所以提示被点掉之后我们把它扔掉（`deferred.value = null`），等浏览器下次觉得
 * 该装了再给一次——`userChoice` 的 outcome 是 `dismissed` 时也一样，那一次机会
 * 已经花掉了。
 *
 * iOS 上永远不会有这个事件：Safari 只认「分享 → 添加到主屏幕」这一条手动路径。
 * 而且 iOS 上的浏览器全部是 WebKit，Chrome iOS 也一样走分享菜单。
 */

import { computed, ref, shallowRef } from 'vue'

/** Chromium 的 `beforeinstallprompt` 事件。TS 的标准库里没有它。 */
export interface BeforeInstallPromptEvent extends Event {
  /** 还能装在哪些平台上（Chromium 目前只给 'web'）。 */
  readonly platforms?: string[]
  /** 用户在原生安装弹窗里点了什么。 */
  readonly userChoice: Promise<{ outcome: 'accepted' | 'dismissed'; platform: string }>
  /** 弹出浏览器自己的安装确认框。同一个事件只能调一次。 */
  prompt(): Promise<void>
}

/** 浏览器刚递给我们的那次安装机会；没机会时为 null。 */
const deferred = shallowRef<BeforeInstallPromptEvent | null>(null)

/** 现在是不是以「已安装应用」的形态在跑。 */
const standalone = ref(false)

/** 有没有人调用过 watchInstallPrompt()（重复调用不会重复挂监听）。 */
let watching = false

/** 浏览器报出来的、用来判定 display-mode 的媒体查询。 */
let displayModeQuery: MediaQueryList | null = null

/**
 * 此刻是不是「已经装好了」。
 *
 * 两个判据都要看：`display-mode: standalone` 是标准那一份（Android WebAPK、
 * 桌面端都认），而 iOS Safari 至今不匹配它——那边只有它自己的
 * `navigator.standalone`。
 */
export function detectStandalone(): boolean {
  if (typeof window === 'undefined') return false
  if (window.matchMedia?.('(display-mode: standalone)')?.matches) return true
  return (navigator as Navigator & { standalone?: boolean }).standalone === true
}

/** 是不是 iOS / iPadOS——那边没有 `beforeinstallprompt`，只能走分享菜单。 */
export function detectIos(): boolean {
  if (typeof navigator === 'undefined') return false
  if (/iPad|iPhone|iPod/.test(navigator.userAgent)) return true
  // iPadOS 13 起自称 Mac（userAgent 里连 iPad 都不出现），触摸点数才是破绽：
  // 真的 Mac 没有触摸屏。
  return navigator.platform === 'MacIntel' && (navigator.maxTouchPoints ?? 0) > 1
}

/**
 * 挂上监听。在 main.ts 里调一次，越早越好——事件只在那之前触发才会被接住。
 *
 * 返回一个卸载函数（测试用；应用里挂一次就行，不卸）。
 */
export function watchInstallPrompt(): () => void {
  if (typeof window === 'undefined' || watching) return () => {}
  watching = true
  standalone.value = detectStandalone()

  const onBeforeInstall = (event: Event) => {
    // 不 preventDefault 的话，Chromium 会自己弹它那条迷你的安装提示，两边打架。
    // 挡下来之后就由我们在设置页里决定什么时候问。
    event.preventDefault()
    deferred.value = event as BeforeInstallPromptEvent
  }
  const onInstalled = () => {
    deferred.value = null
    standalone.value = true
  }

  window.addEventListener('beforeinstallprompt', onBeforeInstall)
  window.addEventListener('appinstalled', onInstalled)

  // display-mode 会变（从浏览器里打开的同一个页面，装好之后下次就是 standalone
  // 了），而 `navigator.standalone` 不参与响应式，所以盯住媒体查询。
  displayModeQuery = window.matchMedia?.('(display-mode: standalone)') ?? null
  displayModeQuery?.addEventListener?.('change', () => (standalone.value = detectStandalone()))

  return () => {
    window.removeEventListener('beforeinstallprompt', onBeforeInstall)
    window.removeEventListener('appinstalled', onInstalled)
    displayModeQuery = null
    watching = false
  }
}

/** 现在有得装、而且浏览器愿意让我们自己问。 */
export const canPromptInstall = computed(() => deferred.value !== null)

/** 已经装过了（这一条决定了设置页里那一格显示什么）。 */
export const isInstalled = computed(() => standalone.value)

/**
 * 弹出浏览器的安装确认框。
 *
 * 返回用户的选择，`unavailable` 表示这一次没有可用的机会（没有事件，或者
 * 浏览器抛了）。三种结果都要让调用方看得见——「点了没反应」正是我们要避免的。
 */
export async function promptInstall(): Promise<'accepted' | 'dismissed' | 'unavailable'> {
  const event = deferred.value
  if (!event) return 'unavailable'
  // 先扔掉：下面无论走哪条路，这个机会都已经花掉了。
  deferred.value = null
  try {
    await event.prompt()
    const choice = await event.userChoice
    return choice?.outcome === 'accepted' ? 'accepted' : 'dismissed'
  } catch {
    // 事件过期、被浏览器拒绝、或者用户直接关掉了原生弹窗。
    return 'unavailable'
  }
}

/** 只给测试用：把模块级状态复位，免得用例之间互相带。 */
export function __resetInstallPromptForTests(): void {
  deferred.value = null
  standalone.value = false
  watching = false
  displayModeQuery = null
}
