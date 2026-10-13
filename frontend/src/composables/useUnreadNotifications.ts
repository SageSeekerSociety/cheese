import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useEventListener } from '@vueuse/core'

import { getUnreadNotificationCount } from '@/api/notifications'
import { firstPaintDone } from '@/lib/afterFirstPaint'
import AccountService from '@/services/account'

// 未读通知数：全站一份。首页那一格用它画小点，待办页的「动态」读完、标已读之后
// 把新数写回来——两处读的是同一个值，所以标完已读，小点当场消失。
//
// 没有推送通道告诉它「多了一条」或者「你在别处读掉了」。少了下面那两个重新读的
// 时机，这个数就只是登录那一刻的一份快照，而小点会替它说错话：亮着点进去一条也
// 没有（那份未读在别的窗口/设备上读掉了），或者来了新的却一直不亮。于是照待处理
// 件数那一份的做法，在人**做了点什么**的时候再读一次：回到待办页、窗口重新拿到
// 焦点。回到待办页那一下不受节流——人正要对着那份列表看，小点必须当场对上。
const count = ref(0)
let watching = false

/** 焦点会来回切，而这个数晚几十秒变不要紧：两次读之间至少隔这么久。 */
const MIN_INTERVAL_MS = 30_000
let lastAt = 0

async function fetchCount() {
  if (!AccountService._loggedIn.value) return
  lastAt = Date.now()
  try {
    count.value = (await getUnreadNotificationCount()).count
  } catch {
    // 保持原数：一次网络抖动不该把小点擦掉。
  }
}

/** 调用方要它当场跟上（标了已读、删了一条、回到待办页）：不受节流。 */
function refresh() {
  return fetchCount()
}

/** 顺手做的那一下（窗口重新拿到焦点）：刚读过就不必再问。 */
async function refreshIfStale() {
  if (Date.now() - lastAt < MIN_INTERVAL_MS) return
  await fetchCount()
}

function set(next: number) {
  count.value = next
}

export function useUnreadNotifications() {
  const route = useRoute()
  if (!watching) {
    watching = true
    // 小点是装饰：登录之后的第一遍排到首帧之后（`lib/afterFirstPaint.ts`），不跟页面
    // 自己要的数据抢。`asked` 挡住的是首帧那个门开了之后重复排的那几遍；退出登录时
    // 清掉，下次登录再问一遍。
    let asked = false
    watch(
      [() => AccountService._loggedIn.value, firstPaintDone],
      ([loggedIn, painted]) => {
        if (!loggedIn) {
          count.value = 0
          asked = false
          return
        }
        if (!painted || asked) return
        asked = true
        void refresh()
      },
      { immediate: true }
    )
    // 走进待办页：人接下来看的就是那份未读列表，先把这个数对上一遍。首帧之前那次不算
    // 「走进」——冷开就落在待办页上时，这一次跳转是挂载之后才落地的，`from` 是起始的
    // `/`；放它过去就会在首帧之前问一遍，门开了再问一遍。门开那一次读的已经是落地后
    // 的状态，不会漏。
    watch(
      () => route.path,
      (path, from) => {
        if (!firstPaintDone.value) return
        if (path === '/inbox' && from !== '/inbox') void refresh()
      }
    )
    useEventListener(window, 'focus', () => void refreshIfStale())
  }
  return { count: computed(() => count.value), refresh, set }
}
