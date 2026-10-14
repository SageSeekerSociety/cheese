// en 那份运行时按需取（理由见下面 `catalogs`），所以这里只借它的类型：`t()` 的键、
// `i18n.global.t` 的返回从两份 schema 上推，而编译后这个 import 一个字都不剩，不会
// 把英文那份挂回入口。
import type en from './messages/en'

import { watch } from 'vue'
import { createI18n } from 'vue-i18n'

// zh-CN 跟着入口一起下（理由见下面 `catalogs`）。
import zhCN from './messages/zh-CN'

import { storageGet, storageSet } from '@/lib/browserStorage'

export type Locale = 'zh-CN' | 'en'
// 跟着入口走的那一份语言包（见下面 `catalogs`）。它是英文界面缺键时的兜底，也是语言
// 包取不回来时整个实例退回的那一门。
const ENTRY_LOCALE: Locale = 'zh-CN'
// Signed in, the account's language is the choice (`services/account.ts`) and
// this is its copy in the browser, read before the account is. Signed out, it is
// the only place a choice is kept.
const preferenceKey = 'cheese:locale'

export function isLocale(value: unknown): value is Locale {
  return value === 'en' || value === 'zh-CN'
}

/** The language this browser keeps, or null when it was never picked here and
 *  the page only follows the browser's own language. */
export function storedLocale(): Locale | null {
  const saved = storageGet(preferenceKey)
  return isLocale(saved) ? saved : null
}

export function resolveInitialLocale(): Locale {
  const saved = storedLocale()
  if (saved) return saved
  return typeof navigator !== 'undefined' && navigator.language.toLowerCase().startsWith('zh') ? 'zh-CN' : 'en'
}

type CatalogSchema = { 'zh-CN': typeof zhCN; en: typeof en }
type Catalog = CatalogSchema[Locale]

// 两份语言包各是一大团消息，入口里最大的一块（962,397 B）差不多全是它们：英文那份
// 单独拿出去是入口之外的 en-*.js（473,897 B），同一块只剩 488,899 B。而一次打开只用
// 得到当前语言那一份。所以 en 不跟入口走，谁要谁取（浏览器语言是英文、账号存的是英文，
// 或者有人切过去），首屏就不再白背它——它是 `import()`，Rolldown 自然把它切成入口之外
// 的一块，不用在 vite.config.ts 的 CHUNKS 里点名。
//
// zh-CN 留在入口里，两个理由：它是 `fallbackLocale`——英文界面缺键时要有一份兜底的
// 中文，动态取的那份填不上这个位；它也是入口那一门（`ENTRY_LOCALE`）——浏览器语言是
// 中文的会话一开始就用它，少一次「先下入口、再发现还要下语言包」的往返。
const catalogs: Record<Locale, () => Promise<Catalog>> = {
  'zh-CN': () => Promise.resolve(zhCN),
  en: () => import('./messages/en').then((m) => m.default),
}

const registered = new Set<Locale>()
const loading = new Map<Locale, Promise<void>>()

/** 没人在等结果的那几次取包：开机那一次，和账号上那条语言跟过来的那一次
 *  （`services/account.ts`）。它们和别的代码块不一样：都在任何一次导航之外，取不回来时
 *  也各有自己的落点（退回入口那一门／留在原来那一门），所以 `services/staleBuild.ts` 的
 *  全局监听看不出它们和「这一页比部署旧了」有什么区别——服务器一直不给这一块，就会被
 *  拿整页刷新一遍一遍去救。取着的这一段时间里它们的失败不归那边管，页面留在手上就好。
 *  取完（无论成败）就恢复；网络卡着一动不动时会一直开着，那一次取包也一直挂着，刷新救
 *  不了它。人自己点的那一次（`chooseLocale`）不从这里取：那一下有人在等。 */
let unattendedPacks = 0

/** 有没有「没人在等结果」的语言包正取着（见上）。 */
export function unattendedPackIsLoading(): boolean {
  return unattendedPacks > 0
}

/** 取到 `locale` 的语言包并交给 vue-i18n。取过的当场返回，正在取的复用同一次
 *  promise——挂载前的 `localeReady` 和换语言都从这里走，不会各取一份。
 *
 *  `unattended`：这一次取没人在等结果，见上。 */
export function loadLocale(locale: Locale, unattended = false): Promise<void> {
  const pack = fetchCatalog(locale)
  if (!unattended) return pack
  unattendedPacks += 1
  const settled = () => {
    unattendedPacks -= 1
  }
  pack.then(settled, settled)
  return pack
}

/** 一份语言包真正的取包：按语言各只跑一次，后来的人复用同一次（见 `loadLocale`）。 */
function fetchCatalog(locale: Locale): Promise<void> {
  if (registered.has(locale)) return Promise.resolve()
  const running = loading.get(locale)
  if (running) return running
  const fetching = catalogs[locale]()
    .then((messages) => {
      i18n.global.setLocaleMessage(locale, messages)
      registered.add(locale)
    })
    .finally(() => loading.delete(locale))
  loading.set(locale, fetching)
  return fetching
}

const initialLocale = resolveInitialLocale()

const i18n = createI18n({
  legacy: false,
  locale: initialLocale,
  fallbackLocale: ENTRY_LOCALE,
  missingWarn: import.meta.env.DEV,
  fallbackWarn: import.meta.env.DEV,
  // 进来就有 zh-CN；en 由 `loadLocale` 在挂载前填进去（`main.ts` 等 `localeReady`）。
  // 断言成 CatalogSchema 只为让 `t()` 认得两份的键——多出来的 en 是运行时才有的。
  messages: { 'zh-CN': zhCN } as CatalogSchema,
})

/** 取不回来时把整个实例退回入口那一门：`locale` 和缺键兜底都是它，界面里不会一半
 *  中文一半英文（日期、vuetify 的词是跟 `locale` 走的）。不写浏览器偏好——这不是谁
 *  选的，只是这次没取到，下次打开还照原样去取。 */
function fallBackToEntryLocale() {
  if (i18n.global.locale.value !== ENTRY_LOCALE) i18n.global.locale.value = ENTRY_LOCALE
}

/** 账号上那一份把界面定在了哪一门，而浏览器偏好里没有留下它（`services/account.ts`
 *  的 `followLanguage`）：账号上说的就是界面这一门时它不换、也不写偏好——那一份不是这个
 *  人在这台机器上选的。别的地方都留着痕迹（人自己选的写了偏好，账号换过去的界面已经不是
 *  入口这一门），只有这一支不留：首屏那份语言包迟到落地时，光看偏好会当成「这期间没人
 *  定过语言」，把界面从账号上这一门翻走。记的是哪一门，因为账号定下的恰好就是这份包要
 *  恢复的那一门时（浏览器和账号都是英文），用上它正是回到账号说的那一门，不该拦。 */
let accountSettledLocale: Locale | null = null

/** 账号上那份语言把界面定在 `locale` 这一门了，见上。 */
export function noteAccountLocaleSettled(locale: Locale) {
  accountSettledLocale = locale
}

/** 这一页上的登录态没了（`services/account.ts` 的 `forget()`）：上面那条记在账号头上的
 *  语言跟着失效。留着一份已经不存在的会话定下的语言，只会把首屏那份迟到的语言包拦在门
 *  外——人退出登录之后，界面这一门该退回浏览器语言那一门（偏好里没记过就是它）。 */
export function forgetAccountLocaleSettled() {
  accountSettledLocale = null
}

/** 首屏那份语言包的最长等待：请求卡在网络上（既不成功也不失败）时也得给挂载一个落点，
 *  不然启动骨架一直挂着。5 秒够慢网把那一块（约 474 KB）取回来，又不至于让人一直等。 */
const BOOT_PACK_WAIT_MS = 5000

/** 等首屏那一份语言包：到位、取不回来、等过头，三种都算落定；后两种退回入口那一门
 *  （`fallBackToEntryLocale`）。`pack` 和 `ms` 只给测试换掉，生产上就是 `localeReady`
 *  那一次——包是 `loadLocale(initialLocale, true)`。 */
export async function awaitBootPack(pack: Promise<unknown>, ms: number = BOOT_PACK_WAIT_MS): Promise<void> {
  let timer: ReturnType<typeof setTimeout> | undefined
  const late = new Promise<void>((resolve) => {
    timer = setTimeout(() => {
      fallBackToEntryLocale()
      resolve()
    }, ms)
  })
  try {
    await Promise.race([pack.catch(() => fallBackToEntryLocale()), late])
  } finally {
    clearTimeout(timer)
  }
  // 期限先到、这一份包之后才到的那一种：界面已经退回入口那一门，而包现在是到了的，该
  // 用上它——不然这一整趟都停在中文，包取回来了也没人再切（`loadLocale` 只登记，不动
  // 界面）。界面到这时还没被别人定下才算：偏好里记着别的语言，说明这期间有人自己选过
  // （账号上那条语言把它换过去的也是走 `setLocale`，同样留下偏好），那一下说了算，不抢；
  // 账号上那条语言把界面定在别的哪一门、没换也没写偏好的那一种，另记在一处
  // （`accountSettledLocale`），也不抢；只有账号定下的正是这份包要恢复的那一门时例外，
  // 那时用上它就是回到账号说的那一门，不算翻走，照旧用上（浏览器和账号都是英文的那位，
  // 包晚到一会儿也该落在英文上）。
  void pack
    .then(() => {
      if (i18n.global.locale.value !== ENTRY_LOCALE) return
      if (accountSettledLocale !== null && accountSettledLocale !== initialLocale) return
      const saved = storedLocale()
      if (saved !== null && saved !== initialLocale) return
      i18n.global.locale.value = initialLocale
    })
    .catch(() => {})
}

/** 首屏那一份语言包到位了：zh-CN 进来就有，en 得等它取回来（四个入口在挂载前等它，
 *  所以 `main.ts` 那份启动骨架要留到挂载）。没人在等它的结果，所以走 `unattended`。 */
export const localeReady = awaitBootPack(loadLocale(initialLocale, true))

export const { t } = i18n.global

export { LANGUAGE_NAMES, LANGUAGE_SWITCH_LABELS, otherLocale } from './languages'

/** 点过多少次语言（`chooseLocale` 进来的次数）。这个只数「人点的」，不数账号那条
 *  `followLanguage` 换的语言：它那条自己就带着新旧判断（`followedRecords`、它落地
 *  前比的那几样），比这里准。 */
let localePicks = 0

/** Show `locale` and remember it in this browser. A person picking a language
 *  goes through `chooseLocale`；`locale` 那份语言包得先在（见 `loadLocale`）。 */
export function setLocale(locale: Locale) {
  i18n.global.locale.value = locale
  // A write that fails still switches for this visit; only the memory is lost.
  storageSet(preferenceKey, locale)
}

const chosen = new Set<(locale: Locale) => void>()

/** A person picked `locale`: 取到那份语言包再换过去——换过去的界面不该有一段先
 *  画裸键的时候——然后告诉 whoever keeps the choice, the account, once signed
 *  in (`services/account.ts`). 取不回来就什么都不换（见下面的 catch）。 */
export async function chooseLocale(locale: Locale) {
  const pick = ++localePicks
  try {
    await loadLocale(locale)
  } catch {
    // 取不回来就什么都不换：界面留在原来那一门，它自己那份包在手上，整个界面是一致
    // 的，偏好也不动，下次点再试（多半是页面比部署旧了，`services/staleBuild.ts`
    // 听到 `vite:preloadError` 会去处理刷新）。
    return
  }
  // 等包这段时间里又点了一次（点回当前显示的那一门也算）：那一下才是他最后要的，
  // 这一次描述的已经是先前那一门了，落地只会把它盖回去——界面、本机偏好和账号都会
  // 记错。只有点语言会进这里，账号那条记录换语言不数（见上面 `localePicks`）。
  if (pick !== localePicks) return
  setLocale(locale)
  for (const listener of chosen) listener(locale)
}

export function onLocaleChosen(listener: (locale: Locale) => void) {
  chosen.add(listener)
}

watch(
  i18n.global.locale,
  (locale) => {
    if (typeof document !== 'undefined') document.documentElement.lang = locale
  },
  { immediate: true }
)

export default i18n
