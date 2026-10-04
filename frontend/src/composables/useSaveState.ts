import { computed, type ComputedRef, onScopeDispose, type Ref, ref } from 'vue'
import { toast } from 'vuetify-sonner'

import { t } from '@/i18n'

/**
 * 一次保存的四态（saving / saved / dirty / error）和跑它的那一个入口（run）。
 *
 * 保存这件事有两种告诉人的方式，由 `feedback` 选（docs/design-system.md §3.11）：
 *
 * - `inline`：内容留在屏幕上的设置区块。结果**就地**说：保存中的那颗按钮转圈，
 *   旁边一行 `SaveStatus` 写「已保存」/「保存失败」，写完自己淡出。换页、刷新都
 *   还在的那一块用它 —— 一条几秒就走掉的 toast，人回头已经看不见了。
 * - `toast`：一次性动作（复制、连接、提交一条马上离开的动作）。结果跟着那一次
 *   动作走，用全局的 toast 说一声就完。
 *
 * `dirty` 由调用方给（本地草稿和上一次保存的值比出来的），它驱动 `SaveBar` 什么时候
 * 展开。`run` 把一个 Promise 包起来：开始时进入 saving，成功进入 saved（`inline` 下
 * 一段时间后自动灭掉，`toast` 下弹一条成功），失败落进 error（`toast` 下弹一条失败）。
 * 同一时刻只跑一个保存；保存中再点不重复发请求。
 */
export type SaveFeedback = 'inline' | 'toast'

export interface SaveStateMessages {
  /** 保存成功后说的话。 */
  saved: string
  /** 保存失败、且错误本身没带原因时的兜底。 */
  failed: string
}

export interface UseSaveStateOptions {
  feedback?: SaveFeedback
  /** 本地草稿是否和已保存的值不同。 */
  dirty?: () => boolean
  /** 覆盖结果文案；默认是全局的「已保存」/「保存失败」。 */
  messages?: Partial<SaveStateMessages>
  /** 把抛出的错误翻成给人看的一句话；默认取它自己的 message。 */
  describeError?: (error: unknown) => string
  /** `inline` 下「已保存」停留多久才淡出，毫秒。 */
  savedDuration?: number
}

export interface SaveState {
  saving: Ref<boolean>
  saved: Ref<boolean>
  dirty: ComputedRef<boolean>
  error: Ref<string>
  /** 跑一次保存。返回动作的结果；失败（已被再说一次）返回 undefined。 */
  run: <T>(action: () => Promise<T>) => Promise<T | undefined>
  /** 清掉回执，例如离开这一块时。 */
  reset: () => void
}

const SAVED_VISIBLE_MS = 2500

export function useSaveState(options: UseSaveStateOptions = {}): SaveState {
  const feedback = options.feedback ?? 'inline'
  const savedMessage = options.messages?.saved ?? t('global.saveState.saved')
  const failedMessage = options.messages?.failed ?? t('global.saveState.failed')
  const describeError =
    options.describeError ??
    ((error: unknown) => (error instanceof Error && error.message ? error.message : failedMessage))

  const saving = ref(false)
  const saved = ref(false)
  const error = ref('')
  const dirty = computed(() => options.dirty?.() ?? false)
  let hideTimer: ReturnType<typeof setTimeout> | undefined

  function clearHideTimer() {
    if (hideTimer !== undefined) {
      clearTimeout(hideTimer)
      hideTimer = undefined
    }
  }

  function reset() {
    clearHideTimer()
    saving.value = false
    saved.value = false
    error.value = ''
  }

  async function run<T>(action: () => Promise<T>): Promise<T | undefined> {
    if (saving.value) return undefined
    clearHideTimer()
    saving.value = true
    saved.value = false
    error.value = ''
    try {
      const result = await action()
      saved.value = true
      if (feedback === 'toast') toast.success(savedMessage)
      else hideTimer = setTimeout(() => (saved.value = false), options.savedDuration ?? SAVED_VISIBLE_MS)
      return result
    } catch (e) {
      const message = describeError(e)
      if (feedback === 'toast') toast.error(message)
      else error.value = message
      return undefined
    } finally {
      saving.value = false
    }
  }

  onScopeDispose(clearHideTimer)

  return { saving, saved, dirty, error, run, reset }
}
