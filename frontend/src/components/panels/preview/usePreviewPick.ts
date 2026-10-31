import type { FramePick, PreviewFrame } from '@/composables/usePreviewFrames'
import type { WebRect, WebViewport } from '@/lib/quotedContext'

import { computed, onBeforeUnmount, ref, watch } from 'vue'

import { t } from '@/i18n'
import { firstCodePoints } from '@/lib/codePoints'
import { isWebPage } from '@/lib/fileKind'
import { MCP_APP_MIME } from '@/lib/mcpAppBridge'
import { webPickLabel } from '@/lib/webPickLabel'

/** 网页预览里的「圈选」开关。
 *
 *  房间的 HTML 文件被注入了桥：帧自己描边、收鼠标，报回选择器和文字，宿主只把开关递进帧
 *  （`onRuntime`）。没有桥的应用（`cheese serve`）由宿主盖一层遮罩（`PreviewRegionPick`）。
 *  图片、PDF 这些走别的查看器，没有可圈的 DOM，`target` 为 null。 */
interface PickOptions {
  frame: () => PreviewFrame | null | undefined
  /** 应用那一档帧上没写地址时的退路。 */
  previewUrl: () => string | null | undefined
  /** 有桥的网页：把开关递进帧。 */
  onRuntime: (on: boolean) => void
  /** 开标注条（会先清掉上一处的引用）。 */
  open: (label: string, quote: string, address: string) => void
  /** 把这一处留成结构化引用，等发送。 */
  quote: {
    web: (pick: FramePick) => void
    region: (payload: { url: string; rect: WebRect; viewport: WebViewport }) => void
  }
  /** 这一段指不指得动（「看 / 批注 / 改」三段）。「改」那一段里圈选开不起来：开关要把
   *  帧切进圈选态、遮罩要收键盘，只拦 `open` 的话这两样已经动过了。 */
  allowed: () => boolean
}

export function usePreviewPick({ frame, previewUrl, onRuntime, open, quote, allowed }: PickOptions) {
  const on = ref(false)
  /** 这一帧能怎么圈，不管此刻让不让圈：关开关时要照它给帧发「关」，哪怕已经切出了批注。 */
  const kind = computed<'runtime' | 'region' | null>(() => {
    const current = frame()
    if (!current) return null
    if (current.live) return 'region'
    // A third party's View has no cheese runtime injected, so there is no pick
    // bridge to switch on — the toggle would do nothing.
    if (current.mime === MCP_APP_MIME) return null
    if (!isWebPage(current.label)) return null
    // SVG 发出去是图，不是 HTML，内容域不往里装桥：帧里没人接开关。和应用一样盖遮罩框一块。
    return /\.svg$/i.test(current.label) ? 'region' : 'runtime'
  })
  // 指不动的那几段（看、改）里连开关都不摆：摆一颗按下去没反应的键比不摆更糟。
  const target = computed(() => (allowed() ? kind.value : null))

  function set(value: boolean) {
    if (value && !allowed()) return
    on.value = value
    if (kind.value === 'runtime') onRuntime(value)
  }
  function toggle() {
    if (target.value) set(!on.value)
  }
  // 切到「改」那一段时把圈选收掉：开着圈选去改正文，鼠标按下去是在圈一块，不是在选字。
  watch(allowed, (ok) => {
    if (!ok && on.value) set(false)
  })

  // 圈选期间 ESC 退出：应用那一档遮罩在宿主这边收键盘；有桥的那一档由帧把 ESC 交回来
  // （见 usePreviewEscape），这里再兜一道，两边都按得下。
  function onKeydown(event: KeyboardEvent) {
    if (event.key !== 'Escape' || !on.value) return
    event.preventDefault()
    set(false)
  }
  window.addEventListener('keydown', onKeydown, true)
  onBeforeUnmount(() => window.removeEventListener('keydown', onKeydown, true))

  // 换了一帧（新导航、切到别的文件）时收掉：上一个文档的圈选不该落在新文档上。
  watch(
    () => frame()?.id,
    () => {
      if (on.value) set(false)
    }
  )

  /** 帧的桥报回来的一处。帧那边报完已经自己关掉了，宿主这一侧的开关也收回来。 */
  function fromFrame(pick: FramePick) {
    set(false)
    // 卡头给人读的名字，引文和别的类型一样只留开头一段；选择器留在引用里交给芝士。
    const label = webPickLabel(pick.label, pick.tag)
    open(label, firstCodePoints(pick.text, 200), label)
    quote.web(pick)
  }

  /** 应用上圈的一块：交出去的是地址 + 几何，回复里没有页面内容。 */
  function fromRegion(payload: { rect: WebRect; viewport: WebViewport }) {
    const url = frame()?.url ?? previewUrl() ?? ''
    set(false)
    if (!url) return
    const label = t('work.room.preview.webRegion')
    open(label, `${Math.round(payload.rect.w)} × ${Math.round(payload.rect.h)}`, label)
    quote.region({ url, ...payload })
  }

  return { on, target, set, toggle, fromFrame, fromRegion }
}
