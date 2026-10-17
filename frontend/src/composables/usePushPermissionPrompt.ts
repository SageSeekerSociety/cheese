// 「本轮运行时间可能较长，完成后通知你」——问推送权限的那一刻（#1084 第 5 步）。
//
// 拆自 `components/PushPermissionPrompt.vue`：什么时候问、问过没有、点了「开启通知」
// 做什么，都在这一个 composable 里；组件只管画那一条和接线——组件不直接碰
// `services/webPush`（`.claude/rules/architecture.md` 的组件边界）。时机本身是这一块
// 的全部内容，由 `components/PushPermissionPrompt.spec.ts` 钉着，搬家没动它。
//
// **为什么不在首屏问。** 首屏上这个人还不知道为什么要授权，多数会拒；而浏览器被拒
// 一次之后基本问不了第二次（`Notification.permission` 变成 `denied`，再调
// `requestPermission` 直接返回 denied，不弹窗）。一次问坏，这个渠道对这个人就永久
// 关闭了。
//
// **所以等到它自己说得通的那一刻**：这个人第一次真的遇到一轮跑过一分钟。那时他正
// 等着结果，而「完成后通知你」是一句他当下就听得懂的话。
//
// 一个浏览器只问一次：答应了不用再问，「暂不开启」是这个人的决定，别追着他问第二
// 次 —— 追问是人关掉一个渠道的头号原因，#1084 为通知定的那条规矩在这里同样成立。
import type { MaybeRefOrGetter } from 'vue'

import { onUnmounted, ref, toValue, watch } from 'vue'

import { storageAvailable, storageGet, storageSet } from '@/lib/browserStorage'
import { enablePush, permissionSettled, pushAvailable, pushSupported } from '@/services/webPush'

//: 一轮跑过这么久才问。#1084：等到这个人第一次遇到「这一轮不是几秒就完」。
const ASK_AFTER_MS = 60_000
//: 问过就不再问 —— 答应了、或者他说了暂不开启。
const ASKED_KEY = 'cheese:push-asked'

/**
 * 这一条什么时候冒出来，以及两颗按钮做什么。
 *
 * @param working 芝士此刻是不是在这个房间里运行（宿主按轮次生命周期给）。
 */
export function usePushPermissionPrompt(working: MaybeRefOrGetter<boolean>) {
  const shown = ref(false)
  const busy = ref(false)
  let timer: ReturnType<typeof setTimeout> | null = null

  function asked(): boolean {
    // 读都读不回来时（站点数据被禁）连「问过没有」都记不住，当作问过，别追着问：追问
    // 是人关掉一个渠道的头号原因，#1084 为通知定的那条规矩在这里同样成立。
    // 探针只看读得到读不到 —— 「读得到、写不进」那一种它探不出来，见 remember。
    if (!storageAvailable()) return true
    return storageGet(ASKED_KEY) === '1'
  }

  function remember() {
    // 写不进去（配额满、Safari 隐私模式）就只在这一轮里算数，下一次跑过一分钟还会再问
    // 一次；装上软提示前就是这样，这次搬家没改它。
    storageSet(ASKED_KEY, '1')
  }

  function stopTimer() {
    if (timer !== null) clearTimeout(timer)
    timer = null
  }

  watch(
    () => toValue(working),
    (isWorking) => {
      stopTimer()
      // 它说的是「这次运行」：这一轮收工了，这句话就不再是真的，没答的也一起收起。
      // 没答不算问过 —— 下一次跑过一分钟的轮次还会再问。
      if (!isWorking) {
        shown.value = false
        return
      }
      if (shown.value) return
      if (!pushSupported() || permissionSettled() || asked()) return
      timer = setTimeout(async () => {
        // 一分钟到了再问一次这个部署开不开推送：这一步要打后端，放在计时之前等于每
        // 开一轮就白问一次。
        // 问后端的那一下里这一轮可能已经收工了，所以拿到答复之后再看一次。
        if (toValue(working) && (await pushAvailable()) && toValue(working)) shown.value = true
      }, ASK_AFTER_MS)
    },
    // `immediate` 是必须的：打开一个**已经在跑**的房间时，`working` 一进来就是 true
    // 而此后不再变化，靠变化触发的话这一格永远不会计时 —— 而「打开时它已经在跑」正
    // 是人回到一轮长活上的常态。
    { immediate: true }
  )

  onUnmounted(stopTimer)

  async function accept() {
    busy.value = true
    // 先记下问过了，再去问权限：不论浏览器那一步是同意还是拒绝，这一次「问」都已经
    // 发生了，而拒绝恰恰是最不能重来的那种结果。
    remember()
    await enablePush()
    busy.value = false
    shown.value = false
  }

  function decline() {
    remember()
    shown.value = false
  }

  return { shown, busy, accept, decline }
}
