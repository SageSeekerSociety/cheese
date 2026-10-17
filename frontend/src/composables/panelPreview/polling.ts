// 「预览」这一格的元数据轮询：只问元数据，产物没变就不会重新提交一次授权。
//
// 间隔是活的——屏幕上这一帧不在线时（实例被换掉、或隧道断了）按 2 秒问，指数退到正常的
// 20 秒：应用重启后通常几秒就绪，等满 20 秒等于让人对着白屏多等一个轮询。一回到在线就重新
// 从 2 秒起算，下一次断线同样值得快问。页面在后台时退回正常档——那会儿的快速轮询是白花的。
import type { PanelPreviewProps, PreviewFrames } from './types'

import { onBeforeUnmount, watch } from 'vue'

export interface PreviewPollingDeps {
  props: PanelPreviewProps
  /** 屏幕上这一帧在不在线，决定下一问的间隔。 */
  frames: PreviewFrames
  /** 问一次元数据。`poll` 说这一问是定时轮询起的，不是读者按的。 */
  load: (opts: { silent?: boolean; poll?: boolean }) => Promise<void>
}

/** 这一格的轮询：什么时候起、下一问隔多久、什么时候停。 */
export function usePreviewPolling(deps: PreviewPollingDeps) {
  const { props, frames, load } = deps

  const NORMAL_POLL_MS = 20_000
  const FAST_POLL_MS = 2_000
  let refreshTimer: ReturnType<typeof setTimeout> | null = null
  let pollEpoch = 0
  let fastPolls = 0
  function stopAutoRefresh() {
    pollEpoch += 1
    if (refreshTimer) clearTimeout(refreshTimer)
    refreshTimer = null
    fastPolls = 0
  }
  function nextPollDelayMs() {
    const frame = frames.displayed.value
    if (document.hidden || !frame?.live || frame.connection === 'online') {
      fastPolls = 0
      return NORMAL_POLL_MS
    }
    // 2、4、8、16、20 秒——累起来约一分钟退回正常档。
    return Math.min(FAST_POLL_MS * 2 ** fastPolls++, NORMAL_POLL_MS)
  }
  function schedulePoll() {
    const epoch = pollEpoch
    refreshTimer = setTimeout(async () => {
      if (epoch !== pollEpoch) return
      if (!document.hidden) await load({ silent: true, poll: true })
      if (epoch === pollEpoch) schedulePoll()
    }, nextPollDelayMs())
  }
  watch(
    () => props.active,
    (active) => {
      stopAutoRefresh()
      // 指定了文件的那一格不轮询：它不跟着当前预览走，文件变了靠每一轮收工那一下重读。
      // 页面在后台的那一格也不问：后台标签的轮询是白花的，回到前台自然会重取。
      if (!active || props.path) return
      schedulePoll()
    },
    { immediate: true }
  )
  onBeforeUnmount(stopAutoRefresh)
}
