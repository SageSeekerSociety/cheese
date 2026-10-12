/**
 * 「预览」那一格上那颗「有新内容」的点，和预览指针的兜底轮询。
 *
 * 芝士摆出来的东西不是一条消息，是一个指针（它指着这个房间此刻该看的那一份）。摆出来
 * 的时候「预览」那一格多半没开着，所以这里一边听 socket 报来的那一声，一边自己在那一格
 * 开着的时候每 5 秒问一次指针兜底——那条帧可能正好丢在断线的一小段里，而屏幕上等的就是
 * 它。指针真换了才 `refreshTick` 一下：那一格全房间共用，重复摆同一份不该惊动别的格。
 *
 * 只有任务有这份指针（频道没有电脑在它名下干活，见 `CHANNEL_TABS`），`taskScoped` 为假
 * 时什么都不做。
 */
import type { ComputedRef, Ref } from 'vue'
import type { PreviewInfo } from '../../../cx_types'

import { computed, onMounted, onUnmounted, ref, watch } from 'vue'

import { readPreview } from '@/query/room'

export function usePreviewPointer(options: {
  /** 这一栏读的那段对话：任务页上是这件任务，否则是这个房间。 */
  conversationId: ComputedRef<string | null>
  /** 此刻选中的是哪一格。轮询只在「预览」那一格开着时跑。 */
  active: Ref<string>
  /** 指针真换了一次：+1，拿着它的那一格据此重取。 */
  refreshTick: Ref<number>
  /** 这一栏读的那段对话有没有「预览」这一格。 */
  taskScoped: ComputedRef<boolean>
}) {
  // ---- 「有新内容」 ----
  // 这几条 id 就是整个机制：服务端此刻指着的是谁，这位读者已经在屏幕上看到的又是谁。
  const latest = ref<string | null>(null)
  const seen = ref<string | null>(null)
  // 当前预览指着的那份文件。跑着的应用只有预览那一格画得出来（它是个进程，没有文件
  // 可指），所以点开的是它就去那一格。
  const path = ref<string | null>(null)
  const hasNew = computed(() => !!latest.value && latest.value !== seen.value)

  function markSeen(id?: string | null) {
    latest.value = id ?? null
    seen.value = id ?? null
  }

  // Fetch the POINTER only (no file read, no cookie priming) so the dot can appear
  // while 预览 is not the open tab. Goes through the query cache (`query/room`), so a
  // request the router guard already started for this topic is reused rather than
  // repeated — and the answer here is there the next time the room is opened.
  //
  // 返回的是「这次问到的产物 id」：`null` 是「这个房间没有预览」（一个真看到过的
  // 状态），`undefined` 是「这一问没成」（没话题 id，或者网络断了）——两者不能混，兜
  // 底轮询要拿它分「变了」和「没问成、下次再比」。
  async function poll(opts: { seen?: boolean } = {}): Promise<string | null | undefined> {
    const tid = options.conversationId.value
    if (!tid) return undefined
    let art: PreviewInfo | null = null
    try {
      art = await readPreview(tid)
    } catch {
      // A failed poll is not a state — leave the dot as it was. The real load
      // reports errors; this one only ever adds a hint.
      return undefined
    }
    path.value = art?.path ?? null
    const id = art?.artifact_id ?? null
    // Opening a topic must not greet the reader with a dot for something that was
    // already there before they arrived, and a poll while 预览 is open is looking
    // at it.
    if (opts.seen || options.active.value === 'preview') markSeen(id)
    else latest.value = id
    return id
  }

  // ---- 兜底轮询 ----
  const POLL_MS = 5_000
  let timer: ReturnType<typeof setInterval> | null = null
  // 上一次看到的产物 id。`undefined` = 还没看过；`null` 是「这个房间没有预览」，是一
  // 个真看到过的值，和「还没看过」不是一回事——问失败不能把它擦回「还没看过」，不然
  // 断线后第一个看到的就又被当成基线放过去了。
  let lastPolledId: string | null | undefined = undefined

  async function tick() {
    if (document.hidden) return
    // 「那一格里此刻展示的是哪一份」在问之前先记下来：第一次兜底轮询拿它当基线。不这么
    // 做的话，开格头 5 秒里换的那一份（那条 WS 帧可能正好丢在断线里——这正是这条兜底
    // 要接住的时刻）会被当成「第一次看到的」悄悄放过去，屏幕上还是旧的，直到 20 秒那
    // 一档才追上。
    const shown = seen.value
    const id = await poll()
    // 这一问没成：什么都不动，下一次再比（别把上一次看到的当成没看过）。
    if (id === undefined) return
    if (lastPolledId === undefined) lastPolledId = shown
    if (lastPolledId !== id) {
      lastPolledId = id
      options.refreshTick.value += 1
    }
  }

  function stopPolling() {
    if (timer) clearInterval(timer)
    timer = null
  }
  function syncPolling() {
    stopPolling()
    if (options.active.value !== 'preview' || document.hidden) return
    timer = setInterval(() => void tick(), POLL_MS)
  }
  // 回到前台 / 窗口重新拿到焦点：别等下一个 5 秒，立刻补一次（在预览这一格上时）。
  function refetchOnReturn() {
    if (!document.hidden && options.active.value === 'preview') void tick()
  }
  function onVisibilityChange() {
    syncPolling()
    refetchOnReturn()
  }
  onMounted(() => {
    window.addEventListener('focus', refetchOnReturn)
    document.addEventListener('visibilitychange', onVisibilityChange)
    syncPolling()
  })
  onUnmounted(() => {
    window.removeEventListener('focus', refetchOnReturn)
    document.removeEventListener('visibilitychange', onVisibilityChange)
    stopPolling()
  })
  // 开上 / 离开预览这一格：这格开着才轮询它（见上）。开的那一下也顺手重排一次定时。
  watch(options.active, () => syncPolling())

  // 芝士在房间里摆出来一份东西（对话栏听完 socket 往上报的那一声）：立刻问一次指针，
  // 别等下一次轮询——「预览」那一格开着就顺手重取，没开就只是让那颗「有新内容」的点
  // 冒出来。指针真换了才 `refreshTick`：那一格全房间共用，总览会跟着重取房间产物，同一
  // 份东西被重复摆一次不该惊动它们。
  function previewShown() {
    if (!options.taskScoped.value) return
    const before = seen.value
    void poll().then((id) => {
      if (id === undefined) return
      lastPolledId = id
      // 只有「预览」这一格开着、而且指针真的换了，才需要一个 `refreshTick` 把重取读出
      // 去。没开那一格时，那颗「有新内容」的点（latest）已经把话说完了，别的格（总览
      // 会跟着重取房间产物）不该陪着白跑一趟。
      if (options.active.value === 'preview' && id !== before) options.refreshTick.value += 1
    })
  }

  return { path, hasNew, latest, markSeen, poll, previewShown }
}
