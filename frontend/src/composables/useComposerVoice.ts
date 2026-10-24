// 输入框的语音：说一段，后端听写、整理成字，落在开口时光标所在的地方。频道的输入框和
// 芝士面板的输入框用的是同一套：开口和说完的手势、录音时的键、落字的样子、「还原原话」。
// 字落到哪由外面给的正文（`draft`）、光标（`caret`）和编辑器（`editor`）决定；
// 听写交给外面给的 `hear`（频道走话题，芝士走自己的那一条）。
import type { Ref } from 'vue'
import type ComposerEditor from '@/components/room/ComposerEditor.vue'

import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'

import { useVoiceInput, type VoiceHeard } from '@/composables/useVoiceInput'

import { t } from '@/i18n'

export function useComposerVoice(opts: {
  draft: Ref<string>
  caret: Ref<number>
  editor: Ref<InstanceType<typeof ComposerEditor> | null>
  /** 这里能不能说（频道要有话题、给了听写）。 */
  enabled: () => boolean
  hear: (wav: Blob) => Promise<VoiceHeard>
  /** 把说出来的「@名字」展开成标签；没有名册就原样。 */
  expand: (text: string) => string
  isImeKey: (e: KeyboardEvent) => boolean
}) {
  const draft = opts.draft
  const caret = opts.caret
  // ---- 语音输入 ----
  //
  // 说一段，后端听写、整理成字，落在开口时光标所在的地方（人这期间接着打字，那个位置
  // 跟着走）。落下来的是整理后的那一版；旁边那颗「还原原话」换成一字不差的转写，再点
  // 换回来。整理后和原话里的「@名字」都按名册展开成标签，和手打的 @ 一样。
  const VOICE_MAX_SECONDS = 60 // 和后端 voice_max_seconds 同一个数
  const SWAP_LINGER_MS = 8000
  const canVoice = computed(
    () =>
      opts.enabled() &&
      typeof navigator !== 'undefined' &&
      !!navigator.mediaDevices?.getUserMedia &&
      typeof window.AudioContext === 'function'
  )
  /** 刚落下的一段：在正文里的起点、两种写法、现在显示的是哪一种。 */
  let landed: { at: number; polished: string; raw: string; showing: 'polished' | 'raw' } | null = null
  const voiceSwap = ref<'raw' | 'polished' | null>(null)
  let swapTimer = 0
  let ownEdit = false

  const voice = useVoiceInput({
    maxSeconds: VOICE_MAX_SECONDS,
    hear: (wav) => opts.hear(wav),
    onHeard: (heard) => landVoice(heard.text, heard.raw),
  })
  const voiceOpen = computed(() => voice.state.value !== 'idle')

  // 一开口就在光标处放一枚占位：录着时写「正在听」，整理中写「正在整理」——字会落在这儿。
  // 回到空闲（落了字、取消、出错）就收起。
  watch(voice.state, (now, was) => {
    if (now === 'recording')
      opts.editor.value?.voiceMarks({ slot: caret.value, slotLabel: t('work.room.composer.voice.listening') })
    else if (now === 'thinking')
      opts.editor.value?.voiceMarks({ slotLabel: t('work.room.composer.voice.thinking'), slotThinking: true })
    else if (was) opts.editor.value?.voiceMarks({ slot: null })
  })

  function writeAt(at: number, from: string, to: string) {
    ownEdit = true
    draft.value = draft.value.slice(0, at) + to + draft.value.slice(at + from.length)
    caret.value = at + to.length
    void nextTick(() => (ownEdit = false))
  }

  function landVoice(text: string, raw: string) {
    const at = Math.min(opts.editor.value?.voiceSlot() ?? caret.value, draft.value.length)
    const polished = opts.expand(text.trim())
    const exact = opts.expand(raw.trim())
    if (!polished) return
    writeAt(at, '', polished)
    // 记号按编辑器里的正文算位置，要等它把这段字放进去之后再标。
    void nextTick(() => {
      opts.editor.value?.voiceMarks({ slot: null, landed: [at, at + polished.length] })
      reveal(polished.length)
    })
    landed = { at, polished, raw: exact, showing: 'polished' }
    showSwap(polished !== exact ? 'raw' : null)
    opts.editor.value?.focus()
  }

  // 落字：整段先占好位置（输入框一次长到位，不一个字一个字地撑高），再从头逐字显出来，
  // 每 28ms 两个字，前沿四个字半透明。显完了那层琥珀浅底再慢慢退掉。减弱动效时一次显完。
  const REVEAL_STEP_MS = 28
  let revealTimer = 0
  function reveal(length: number) {
    window.clearInterval(revealTimer)
    if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return
    let shown = 0
    opts.editor.value?.voiceMarks({ revealed: 0 })
    revealTimer = window.setInterval(() => {
      shown += 2
      if (shown >= length) {
        window.clearInterval(revealTimer)
        opts.editor.value?.voiceMarks({ revealed: null })
      } else opts.editor.value?.voiceMarks({ revealed: shown })
    }, REVEAL_STEP_MS)
  }
  onBeforeUnmount(() => window.clearInterval(revealTimer))

  function showSwap(to: 'raw' | 'polished' | null) {
    window.clearTimeout(swapTimer)
    voiceSwap.value = to
    if (to) swapTimer = window.setTimeout(() => (voiceSwap.value = null), SWAP_LINGER_MS)
  }

  /** 换成另一种写法：只换那一段，前后别人打的字不动。那一段已经被改过就不换了。 */
  function swapVoice() {
    if (!landed) return
    const shown = landed.showing === 'polished' ? landed.polished : landed.raw
    const next = landed.showing === 'polished' ? landed.raw : landed.polished
    if (draft.value.slice(landed.at, landed.at + shown.length) !== shown) return showSwap(null)
    writeAt(landed.at, shown, next)
    landed.showing = landed.showing === 'polished' ? 'raw' : 'polished'
    const at = landed.at
    void nextTick(() => opts.editor.value?.voiceMarks({ landed: [at, at + next.length] }))
    showSwap(landed.showing === 'polished' ? 'raw' : 'polished')
    opts.editor.value?.focus()
  }

  // 人自己动了正文，那颗换写法的按钮就该走了：它说的那一段可能已经不是它了。
  watch(draft, () => {
    if (ownEdit || !voiceSwap.value) return
    showSwap(null)
    landed = null
  })

  // 按住说话：一开始录，动作行就换成录音条，按着的那颗麦克风连同它抓着的指针一起被卸掉，
  // 它自己再也听不到松手。所以松手在 window 上等，和按钮在不在无关。
  // 只认按着麦克风的那根手指：另一根手指在别处点一下、滚一下，不算松手。录音一结束（说完、
  // 取消、出错）就不再等，免得这一次的松手去结束下一段录音。系统把指针收走（来电、系统
  // 手势）不是「说完了」，这一段丢掉，免得半句话落进正文。
  //
  // 按着的时候往左拖：录音条跟着手指挪（阻尼一半），拖过 90px 就「松手取消」，拖回来又恢复。
  let stopHold: (() => void) | null = null
  const DROP_PX = 90
  const holding = ref(false)
  const dragX = ref(0)
  const armed = ref(false)
  function onVoice(how: 'tap' | 'hold' | 'release' | 'cancel', pointerId?: number, x?: number) {
    if (how === 'release') {
      if (armed.value) {
        stopHold?.()
        return void voice.cancel()
      }
      return void voice.finish()
    }
    if (how === 'cancel') {
      stopHold?.()
      voice.cancel()
      return
    }
    // 一段录音没完，就不再开第二段：第二根手指在动作行换走之前也按在麦克风上，不算新的开始。
    if (voice.state.value !== 'idle') {
      if (how === 'tap' && voice.state.value === 'recording') void voice.finish()
      return
    }
    if (how === 'hold') {
      stopHold?.()
      const release = (e: PointerEvent) => {
        if (pointerId !== undefined && e.pointerId !== pointerId) return
        const drop = armed.value
        stopHold?.()
        if (drop) voice.cancel()
        else void voice.finish()
      }
      const cancel = (e: PointerEvent) => {
        if (pointerId !== undefined && e.pointerId !== pointerId) return
        stopHold?.()
        voice.cancel()
      }
      const move = (e: PointerEvent) => {
        if ((pointerId !== undefined && e.pointerId !== pointerId) || x === undefined) return
        const dx = Math.min(0, e.clientX - x)
        dragX.value = Math.max(-DROP_PX, dx / 2)
        armed.value = dx < -DROP_PX
      }
      window.addEventListener('pointerup', release)
      window.addEventListener('pointercancel', cancel)
      window.addEventListener('pointermove', move)
      holding.value = true
      stopHold = () => {
        window.removeEventListener('pointerup', release)
        window.removeEventListener('pointercancel', cancel)
        window.removeEventListener('pointermove', move)
        holding.value = false
        dragX.value = 0
        armed.value = false
        stopHold = null
      }
    }
    void voice.start()
  }
  watch(voice.state, (now) => {
    if (now !== 'recording') stopHold?.()
  })
  onBeforeUnmount(() => stopHold?.())

  /** 录音时的键：Enter 说完、Esc 不要了；Alt+V 开始，录着时再按一次说完。认领了返回 true。 */
  function onVoiceKey(e: KeyboardEvent): boolean {
    if (e.altKey && e.code === 'KeyV' && canVoice.value && !opts.isImeKey(e)) {
      e.preventDefault()
      if (voice.state.value === 'idle') void voice.start()
      else void voice.finish()
      return true
    }
    if (!voiceOpen.value) return false
    if (e.key === 'Escape') {
      e.preventDefault()
      voice.cancel()
      return true
    }
    if (e.key === 'Enter' && !e.shiftKey && !opts.isImeKey(e)) {
      e.preventDefault()
      void voice.finish()
      return true
    }
    return false
  }

  return {
    VOICE_MAX_SECONDS,
    canVoice,
    voice,
    voiceOpen,
    voiceSwap,
    swapVoice,
    onVoice,
    onVoiceKey,
    holding,
    dragX,
    armed,
  }
}
