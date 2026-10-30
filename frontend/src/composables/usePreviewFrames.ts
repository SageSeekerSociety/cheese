import type { PreviewSession } from '../api'

import { computed, nextTick, onBeforeUnmount, ref } from 'vue'

import { frameKeys, runFrameKey } from '../commands/shortcuts'
import { t } from '../i18n'
import { createMcpAppBridge, MCP_APP_MIME, type McpAppBridge, type McpAppMessage } from '../lib/mcpAppBridge'
import { postPreviewSession } from '../lib/previewSession'

export interface PreviewFrame {
  id: number
  name: string
  url: string
  label: string
  mime: string
  version: string | null
  live: boolean
  posted: boolean
  identity?: string
  resourceId?: string
  instance?: string
  runtime?: 'unconfirmed' | 'ready' | 'failed'
  connection?: 'online' | 'disconnected' | 'gone'
  /** 这一帧自己那次导航没装成（发送失败、超时、文档报错）。屏幕上留下的还是上一帧。 */
  navigationFailed?: boolean
  runtimeError?: string
  /** 这一份资源允许等多久，见 `navigationBudget()`。不填＝默认那档。 */
  budgetMs?: number
  /** 帧里的运行时桥报来的此刻地址：应用里点链接、SPA 改路由都跟着变。没装桥的页面
   *  一直不给，地址栏退回显示入口。 */
  href?: string
  title?: string
  /** 能不能后退、前进：帧说得出（Navigation API）就照它，说不出由这一层自己数。 */
  canBack?: boolean
  canForward?: boolean
  /** 这一份文档里有运行时桥在应答：地址栏、后退前进、快照都要它。 */
  bridged?: boolean
  /** 页面自己抛出来的错（由帧里的桥收着报回来，最多 5 条）。 */
  pageErrors?: PageError[]
}

/** 页面里一个未捕获的错误：说什么、哪个文件、第几行。 */
export interface PageError {
  message: string
  source: string
  line: number
}

/** 帧里那一页的静态快照：渲染后的 DOM，样式内联、脚本和外链拿掉（见运行时桥的
 *  `snapshotHtml`）。应用睡着时右栏拿它顶上。 */
export interface FrameSnapshot {
  html: string
  href: string
  title: string
  viewport: { w: number; h: number }
}

/** 这一帧是为哪一件产物授权的：身份串的第 3 项（见 `currentPreview` 里怎么拼的）。
 *
 *  换件时状态里那个「现在这一件」的指针先动、新文档才在路上，这中间旧帧仍然在台前、
 *  报上来的快照还是旧件的页面——存快照的人拿这个来确认「这张确实是那一件拍的」。
 *  读不出来（还没授权、串坏了）就没有答案。 */
export function frameArtifactId(frame: Pick<PreviewFrame, 'identity'> | null | undefined): string | null {
  if (!frame?.identity) return null
  try {
    const parts = JSON.parse(frame.identity)
    return Array.isArray(parts) && typeof parts[2] === 'string' ? parts[2] : null
  } catch {
    return null
  }
}

/** 地址换了以后等多久再取快照：页面多半还在补数据，等它安静一点。 */
const SNAPSHOT_SETTLE_MS = 2_000

export type PreviewNavigation = 'idle' | 'authorizing' | 'navigating' | 'loaded' | 'failed'

/** 连接状态的一次转折，报给取数那一层：实例被换掉（要跟到新实例），或同一实例断线
 *  后又回来（这段断线里的请求都失败了，要重载一次）。状态没变就是 null。 */
export type ConnectionChange = 'instance-changed' | 'instance-recovered' | null

/** 一整页的默认预算。 */
const FILE_NAVIGATION_BUDGET_MS = 30_000

/** 应用要过隧道，还可能赶上机器冷启动（座位那次实测 2 分 17 秒），默认那 30 秒会把它
 *  误报成「加载超时」。130 秒是 CC 自己的默认档（`13e4`）。 */
export const APP_NAVIGATION_BUDGET_MS = 130_000

/** 监听实例指纹：sha256(boot_id:port:socket inode) 的十六进制串（后端也按这个形状校验）。
 *  探针没回来时后端会给出 `url` 但不给 `instance`——「没有证据」不是「换了一个实例」，
 *  认错会把授权签成一张没绑资源的空头支票，所以只有两个合法指纹不同才算换实例。 */
const INSTANCE_FINGERPRINT = /^[0-9a-f]{64}$/
function isInstanceFingerprint(value: string | null | undefined): value is string {
  return typeof value === 'string' && INSTANCE_FINGERPRINT.test(value)
}

/** 硬顶和余量同样照 CC：正数先被夹进 10 分钟，再留 2 秒给最后那一程。 */
const MAX_NAVIGATION_BUDGET_MS = 600_000
const NAVIGATION_BUDGET_SLACK_MS = 2_000

/** 这一份资源是哪个档，单位毫秒：调用方给的正数被夹进 10 分钟硬顶；没给、或者给的
 *  是 0、负数、`NaN`、`Infinity`，退回文件档——这些值不是「立刻失败」，是「没填」。
 *
 *  文案说的是这个数：那 2 秒余量是我们的，不是这一份资源的。 */
export function navigationTier(requested?: number): number {
  return typeof requested === 'number' && Number.isFinite(requested) && requested > 0
    ? Math.min(requested, MAX_NAVIGATION_BUDGET_MS)
    : FILE_NAVIGATION_BUDGET_MS
}

/** 帧是另一个源上的文档，报上来的东西一律当数据看：认不出形状就丢掉。 */
function pageError(value: unknown): PageError | null {
  if (!value || typeof value !== 'object') return null
  const data = value as Record<string, unknown>
  const message = frameText(data.message, 300)
  if (!message) return null
  return { message, source: frameText(data.source, 200), line: frameNumber(data.line) }
}

/** 一次导航真正等多久，单位毫秒——档位，再加上最后那 2 秒余量（照 CC 的消费式）。
 *  多出来的这 2 秒是给「就快好了」那一程和 500 毫秒的轮询粒度留的，不进文案。 */
export function navigationBudget(requested?: number): number {
  return typeof requested === 'number' && Number.isFinite(requested) && requested > 0
    ? navigationTier(requested) + NAVIGATION_BUDGET_SLACK_MS
    : navigationTier(requested)
}

export interface PreviewFrameOptions {
  /**
   * 帧里的 ESC 交回宿主时叫一次。怎么处理由画的那一半决定（关标注条、退全屏、把
   * 焦点收回面板），这一层只管把这件事报上来。
   */
  onEscape?: () => void
  /**
   * 帧里圈选了一处（点中一个元素、或者选了一段文字）时叫一次。帧的桥把这一处量好
   * 报上来，画不画标注条、发不发引用由画的那一半决定。
   */
  onPick?: (pick: FramePick) => void
  /**
   * 应用那一帧留了一份静态快照时叫一次（装上、确认就绪、换了地址之后各取一次）。存哪儿
   * 由取数那一层定；只有实时的应用才取——静态文件不会睡着。
   */
  onSnapshot?: (frame: PreviewFrame, snapshot: FrameSnapshot) => void
  /** 那一页拍下来了，但超过快照上限、留不下。和「还没有快照」是两种不同的原因。 */
  onSnapshotTooLarge?: (frame: PreviewFrame) => void
  /**
   * 第三方 View 里的表单点提交（`ui/message`）时叫一次。这一层只把解出来的文本报上来，
   * 拼成哪一条消息、发去哪儿是取数那一层和宿主的事。
   */
  onAppMessage?: (message: McpAppMessage) => void
}

/** 帧的桥报上来的一处圈选：位置（选择器 / 标签）和当时量出来的几何。
 *
 *  `selection` 区别「点了一个元素」和「选了一段文字」：后者多带两侧各 32 个字符，
 *  用来分辨同一句话在页面上的哪一处出现。`rect` 是视口像素坐标，`viewport` 是量它
 *  当时的视口大小——页面按视口重排，少了它 `rect` 说的哪一版就说不准了。 */
export interface FramePick {
  selection: boolean
  selector: string
  tag: string
  text: string
  prefix: string
  suffix: string
  rect: { x: number; y: number; w: number; h: number }
  viewport: { w: number; h: number }
}

/** 帧是另一个源上的文档，报上来的东西一律当数据看：截长、丢怪形状，认不出就答 null。 */
function frameText(value: unknown, max: number): string {
  return typeof value === 'string' ? value.slice(0, max) : ''
}
function frameNumber(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : 0
}
/** 一处圈选，或者认不出位置时答 null。 */
export function framePick(value: unknown): FramePick | null {
  if (!value || typeof value !== 'object') return null
  const data = value as Record<string, unknown>
  const selector = frameText(data.selector, 256)
  const text = frameText(data.text, 500)
  const selection = data.selection === true
  // 位置认不出（选择器空），或者说是选中的一段却没报原文：都不算一处。
  if (!selector) return null
  if (selection && !text) return null
  const rect = (data.rect ?? {}) as Record<string, unknown>
  const viewport = (data.viewport ?? {}) as Record<string, unknown>
  return {
    selection,
    selector,
    tag: frameText(data.tag, 32),
    text,
    prefix: frameText(data.prefix, 64),
    suffix: frameText(data.suffix, 64),
    rect: { x: frameNumber(rect.x), y: frameNumber(rect.y), w: frameNumber(rect.w), h: frameNumber(rect.h) },
    viewport: { w: frameNumber(viewport.w), h: frameNumber(viewport.h) },
  }
}

/** Incoming navigation never destroys the last observed loaded browsing context. */
export function usePreviewFrames(frameName: string, options: PreviewFrameOptions = {}) {
  const displayed = ref<PreviewFrame | null>(null)
  const incoming = ref<PreviewFrame | null>(null)
  const navigation = ref<PreviewNavigation>('idle')
  const error = ref('')
  const failedIdentity = ref<string | null>(null)
  const frames = computed(() => [displayed.value, incoming.value].filter((frame): frame is PreviewFrame => !!frame))
  let attemptIdentity: string | null = null
  let serial = 0
  let timer: ReturnType<typeof setInterval> | null = null
  let elapsed = 0
  let lastTick = 0
  let visible = false
  let runtimeWindow: Window | null = null
  let runtimeSession = ''
  // The bridge for a third-party View, when the displayed frame is one. Null for
  // the room's own documents, which use the injected `cheese-preview-runtime`.
  let mcpApp: McpAppBridge | null = null
  // 帧说不出能不能后退时自己数的那一摞：这一份文档装上以后走过的地址，和此刻在第几个。
  let trail: string[] = []
  let trailAt = -1
  let snapshotTimer: ReturnType<typeof setTimeout> | null = null

  function sendRuntimeHello(frame: PreviewFrame) {
    try {
      runtimeWindow?.postMessage(
        {
          channel: 'cheese-preview-runtime',
          version: 1,
          type: 'hello',
          sessionId: runtimeSession,
          resourceId: frame.resourceId ?? null,
          // 焦点在帧里时宿主的键盘监听收不到按键，所以键表随握手一起过去：
          // 帧只回 id，跑哪条命令由这边决定。表是活的，每送一次都重取。
          keys: frameKeys(),
        },
        new URL(frame.url).origin
      )
    } catch {
      // A replaced or opaque document cannot acknowledge; remain unconfirmed.
      runtimeWindow = null
    }
  }

  function runtimeMessage(event: MessageEvent) {
    const frame = displayed.value
    if (!frame || !runtimeWindow || event.source !== runtimeWindow) return
    // A third-party View is opaque-origin and speaks its own JSON-RPC: give it to
    // its bridge before the cheese-runtime origin check, which it can never pass.
    if (mcpApp) {
      mcpApp.handle(event)
      return
    }
    if (event.origin !== new URL(frame.url).origin) return
    const data = event.data
    if (!data || data.channel !== 'cheese-preview-runtime' || data.version !== 1) return
    if (data.type === 'hello-request') {
      // An app may install its bridge after iframe load's first hello was sent.
      sendRuntimeHello(frame)
      return
    }
    if (data.sessionId !== runtimeSession) return
    if (data.type === 'key' && typeof data.id === 'string') {
      // 帧只回 id。认不认这条 id 由 `runFrameKey` 拿刚发下去那张表来判。
      runFrameKey(data.id)
    } else if (data.type === 'escape') {
      // 帧里按了 ESC：把控制权要回宿主。不改变就绪状态——注入的页面从不报 ready。
      options.onEscape?.()
    } else if (data.type === 'pick') {
      // 帧里圈选了一处。形状不对的丢掉，别让另一份文档塞进来的怪东西走到标注条。
      const pick = framePick(data)
      if (pick) options.onPick?.(pick)
    } else if (data.type === 'location' && typeof data.href === 'string') {
      located(frame, data)
    } else if (data.type === 'snapshot' && typeof data.html === 'string') {
      if (frame.live && data.reason === 'too-large') {
        // 拍成了、但这一页太大留不下（上限在桥和平台两边同值）：睡着时卡上该说的是
        // 「这一页太大」，不是「还没有留下快照」——后者听着像还没人打开过。
        options.onSnapshotTooLarge?.(frame)
      } else if (data.html && frame.live) {
        options.onSnapshot?.(frame, {
          html: data.html,
          href: frameText(data.href, 2048),
          title: frameText(data.title, 200),
          viewport: {
            w: frameNumber((data.viewport ?? {}).w),
            h: frameNumber((data.viewport ?? {}).h),
          },
        })
      }
    } else if (data.type === 'ready') {
      frame.runtime = 'ready'
      frame.runtimeError = ''
      scheduleSnapshot(frame)
    } else if (data.type === 'error' && typeof data.message === 'string') {
      frame.runtime = 'failed'
      frame.runtimeError = data.message.slice(0, 1000)
    } else if (data.type === 'errors' && Array.isArray(data.items)) {
      // 页面自己抛的错：认得出形状的留下，最多 5 条（桥那边也是这个数）。换页时在
      // `located` 里清掉——新一页的错归新一页。
      // 先收成 `unknown[]`：`Array.isArray` 只收到 `any[]`，后面那条链子就一路是 any，
      // 收错形状的东西也过得了闸门。
      const items: unknown[] = data.items
      frame.pageErrors = items
        .map(pageError)
        .filter((item): item is PageError => item !== null)
        .slice(0, 5)
    }
  }
  window.addEventListener('message', runtimeMessage)

  /** 帧报来一次地址。后退前进按钮亮不亮先听帧的（Navigation API），帧说不出就看自己数
   *  的那一摞：走到上一格算后退、下一格算前进，别的都是新走一步、截掉前面的分叉。 */
  function located(frame: PreviewFrame, data: Record<string, unknown>) {
    const href = frameText(data.href, 2048)
    frame.bridged = true
    frame.href = href
    frame.title = frameText(data.title, 200)
    // 换了地址（应用里点链接、SPA 改路由）：上一页抛的错不归新的一页。
    if (frame.pageErrors?.length) frame.pageErrors = []
    if (trail[trailAt] !== href) {
      if (trail[trailAt - 1] === href) trailAt -= 1
      else if (trail[trailAt + 1] === href) trailAt += 1
      else {
        trail = [...trail.slice(0, trailAt + 1), href]
        trailAt = trail.length - 1
      }
    }
    frame.canBack = typeof data.canBack === 'boolean' ? data.canBack : trailAt > 0
    frame.canForward = typeof data.canForward === 'boolean' ? data.canForward : trailAt < trail.length - 1
    scheduleSnapshot(frame)
  }

  function scheduleSnapshot(frame: PreviewFrame) {
    if (!frame.live || !options.onSnapshot) return
    if (snapshotTimer) clearTimeout(snapshotTimer)
    snapshotTimer = setTimeout(() => {
      snapshotTimer = null
      if (displayed.value?.id === frame.id) command({ type: 'snapshot' })
    }, SNAPSHOT_SETTLE_MS)
  }

  /** 发给屏幕上这一帧的一条命令，只认当前会话（帧那边也只认）。发不出去答 false。 */
  function command(payload: Record<string, unknown>): boolean {
    const frame = displayed.value
    if (!frame || !runtimeWindow) return false
    try {
      runtimeWindow.postMessage(
        { channel: 'cheese-preview-runtime', version: 1, sessionId: runtimeSession, ...payload },
        new URL(frame.url).origin
      )
      return true
    } catch {
      // A replaced or opaque document cannot receive it.
      return false
    }
  }

  /** 让帧进/出圈选。帧那边的桥收到才描边收鼠标；没有注入桥的页面根本收不到，那一档由
   *  宿主自己在 iframe 上盖一层。 */
  function setPickMode(on: boolean) {
    command({ type: 'pick-mode', on: on === true })
  }

  /** 后退、前进、重载帧里那一页：只对装了桥的文档有效，没装的答 false，由调用方退回
   *  整帧重新加载。 */
  function stepHistory(action: 'back' | 'forward' | 'reload'): boolean {
    return !!displayed.value?.bridged && command({ type: 'history', action })
  }

  /** 让帧跳到同一个源里的另一个地址（地址栏里敲的那一个）。别的源帧那边会拒。 */
  function goTo(path: string): boolean {
    return !!displayed.value?.bridged && command({ type: 'navigate', path })
  }

  function observeConnection(instance: string | null | undefined, online: boolean): ConnectionChange {
    const frame = displayed.value
    if (!frame?.live) return null
    const previous = frame.connection
    // 「换了实例」要求两边都是合法指纹且不同：缺 instance（探针没答）时只是没证据，
    // 状态仍是 online，不是 gone。见上面 INSTANCE_FINGERPRINT 的理由。
    const replaced =
      online && isInstanceFingerprint(instance) && isInstanceFingerprint(frame.instance) && instance !== frame.instance
    frame.connection = !online ? 'disconnected' : replaced ? 'gone' : 'online'
    // 状态更新不动这一帧：旧页面一直显示到新一帧装上为止（双缓冲）。gone 每一轮都报，
    // 因为一次跟丢（授权 404、应用还在冷启动）之后还要再试——取数那一层按次数/时间
    // 收口。#2349 说的是授权该拒谁，没变；变的是面板自己注意到了「该换一帧了」。
    if (replaced) return 'instance-changed'
    if (frame.connection === 'online' && previous === 'disconnected') return 'instance-recovered'
    return null
  }

  function stopTimer() {
    if (timer) clearInterval(timer)
    timer = null
    document.removeEventListener('visibilitychange', visibilityChanged)
  }

  function accountTime() {
    const now = performance.now()
    if (visible) elapsed += now - lastTick
    lastTick = now
  }

  function visibilityChanged() {
    // Account the old state before changing it. A hidden page may run no ticks.
    accountTime()
    visible = !document.hidden
  }

  function fail(message: string) {
    stopTimer()
    // 这一程要放上屏幕的正是屏幕上这一帧本身（同一个 identity），它却没装成——那就是
    // 「屏幕上这一帧的导航失败了一次」。留下记号：同实例断线再回来时据此替它重来一遍。
    // 换到别的实例去的那次失败（identity 不同）不算，健康的旧帧不该被连坐。
    const shown = displayed.value
    if (shown && attemptIdentity && shown.identity === attemptIdentity) shown.navigationFailed = true
    incoming.value = null
    navigation.value = 'failed'
    failedIdentity.value = attemptIdentity
    error.value = message
  }

  function authorize(identity?: string) {
    stopTimer()
    incoming.value = null
    attemptIdentity = identity ?? null
    failedIdentity.value = null
    navigation.value = 'authorizing'
    error.value = ''
  }

  async function navigate(
    session: PreviewSession,
    page: Pick<PreviewFrame, 'url' | 'label' | 'mime' | 'version' | 'live' | 'identity' | 'budgetMs'>,
    stillCurrent: () => boolean,
    path?: string
  ) {
    stopTimer()
    attemptIdentity = page.identity ?? null
    const id = ++serial
    incoming.value = {
      ...page,
      url: session.url,
      id,
      name: `${frameName}-${id}`,
      posted: false,
      resourceId: session.resource_id,
      instance: session.resource?.instance,
      runtime: 'unconfirmed',
      connection: 'online',
    }
    navigation.value = 'navigating'
    error.value = ''
    await nextTick()
    if (!stillCurrent() || incoming.value?.id !== id) return
    // about:blank's mount load is not evidence of the authorized navigation.
    incoming.value.posted = true
    elapsed = 0
    lastTick = performance.now()
    visible = !document.hidden
    document.addEventListener('visibilitychange', visibilityChanged)
    // 预算按这一份资源定：一份静态网页 30 秒够了，一个可能正在冷启动的应用不是。
    const budget = navigationBudget(page.budgetMs)
    const tierSeconds = Math.round(navigationTier(page.budgetMs) / 1000)
    timer = setInterval(() => {
      accountTime()
      if (elapsed >= budget) fail(t('work.room.preview.navigationTimeout', { seconds: tierSeconds }))
    }, 500)
    try {
      postPreviewSession(session, { target: incoming.value.name, ...(path ? { path } : {}) })
    } catch (cause) {
      fail(cause instanceof Error ? cause.message : String(cause))
      throw cause
    }
  }

  function loaded(id: number, event: Event) {
    const frame = incoming.value?.id === id ? incoming.value : displayed.value
    if (!frame || frame.id !== id || !frame.posted) return
    if (!(event.target instanceof HTMLIFrameElement) || event.target.name !== frame.name) return
    // A newly mounted blank document can finish after POST was scheduled.
    // Cross-origin content is opaque; only reject a readable blank document.
    try {
      if (event.target.contentDocument?.URL === 'about:blank') return
    } catch {
      // The authorized content origin is intentionally different from the host.
    }
    if (incoming.value?.id === id) {
      stopTimer()
      displayed.value = frame
      incoming.value = null
      failedIdentity.value = null
      navigation.value = 'loaded'
      // 换上的是另一帧：上一帧走过的那一摞不归它。
      trail = []
      trailAt = -1
    }
    // A document reload keeps its frame but must establish a fresh runtime session.
    frame.runtime = 'unconfirmed'
    frame.runtimeError = ''
    // 新的文档要重新证明自己有桥；地址留着上一次的，等新文档报上来再换，免得地址栏一闪。
    // 后退前进的亮不亮一起清掉：新文档还没报过来之前它是「不知道」，留着上一份文档的
    // 值会让按钮亮着而按下去什么也不发生（命令要桥，没桥就答 false）。
    frame.bridged = false
    frame.canBack = false
    frame.canForward = false
    // 旧文档排下的那次「拍张快照」不算数了：帧 id 没变，光看 id 它照样会命中，拿新会话
    // 向刚装上的文档要一张——那时的页面还在补数据，取到的半成品会被当成「最近一次成功
    // 加载」存下来。新文档自己会按新的地址重排一次（`scheduleSnapshot`）。
    if (snapshotTimer) clearTimeout(snapshotTimer)
    snapshotTimer = null
    // 装成了：这一帧上一次的失败（如果有）到此为止。
    frame.navigationFailed = false
    runtimeWindow = event.target.contentWindow
    runtimeSession = crypto.randomUUID()
    if (frame.mime === MCP_APP_MIME && runtimeWindow) {
      // A third party's View: independent JSON-RPC bridge, no injected runtime.
      mcpApp = createMcpAppBridge(runtimeWindow, { name: 'cheese', version: '1' }, { onMessage: options.onAppMessage })
    } else {
      mcpApp = null
      sendRuntimeHello(frame)
    }
  }

  function failed(id: number, event: Event) {
    const frame = incoming.value
    if (!frame || frame.id !== id || !frame.posted) return
    if (!(event.target instanceof HTMLIFrameElement) || event.target.name !== frame.name) return
    fail(t('work.room.preview.navigationFailed'))
  }

  function pause() {
    stopTimer()
    incoming.value = null
    if (navigation.value !== 'failed') navigation.value = displayed.value ? 'loaded' : 'idle'
  }

  function reset() {
    serial += 1
    runtimeWindow = null
    runtimeSession = ''
    mcpApp = null
    stopTimer()
    if (snapshotTimer) clearTimeout(snapshotTimer)
    snapshotTimer = null
    trail = []
    trailAt = -1
    displayed.value = null
    incoming.value = null
    failedIdentity.value = null
    attemptIdentity = null
    navigation.value = 'idle'
    error.value = ''
  }

  onBeforeUnmount(() => {
    reset()
    window.removeEventListener('message', runtimeMessage)
  })
  return {
    displayed,
    incoming,
    frames,
    navigation,
    error,
    failedIdentity,
    observeConnection,
    authorize,
    navigate,
    loaded,
    failed,
    fail,
    pause,
    reset,
    setPickMode,
    stepHistory,
    goTo,
  }
}
