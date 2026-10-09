import type { Ref } from 'vue'

import { onBeforeUnmount, watch } from 'vue'

/**
 * 出错提示停多久。一次没成的事（表情没加上、下载失败）说一句，够读完就淡出：一直
 * 挂着的话它盖住输入框上方那块，而说的多半已经过去了。连不上服务器的时候不走——
 * 那时候这一行说的是房间此刻的状态（连接被拒、正在重连、历史没读出来），它一走，
 * 房间为什么不动就没人说了。
 */
const ERROR_TOAST_MS = 6000

export function useErrorFade(errorMsg: Ref<string | null>, connected: Ref<boolean>, connectRefused: Ref<boolean>) {
  let errorTimer: ReturnType<typeof setTimeout> | undefined
  watch([errorMsg, connected, connectRefused], ([message, online, refused]) => {
    clearTimeout(errorTimer)
    if (message && online && !refused) errorTimer = setTimeout(() => (errorMsg.value = null), ERROR_TOAST_MS)
  })
  onBeforeUnmount(() => clearTimeout(errorTimer))
}
