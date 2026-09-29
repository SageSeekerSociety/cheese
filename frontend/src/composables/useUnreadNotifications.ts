import { computed, ref, watch } from 'vue'

import { NotificationsApi } from '@/network/api/notifications'
import AccountService from '@/services/account'

// 未读通知数：全站一份。首页那一格用它画小点，待办页的「动态」读完、标已读之后
// 把新数写回来——两处读的是同一个值，所以标完已读，小点当场消失。
const count = ref(0)
let watching = false

async function refresh() {
  if (!AccountService._loggedIn.value) return
  try {
    count.value = (await NotificationsApi.getUnreadCount()).data.count
  } catch {
    // 保持原数：一次网络抖动不该把小点擦掉。
  }
}

function set(next: number) {
  count.value = next
}

export function useUnreadNotifications() {
  if (!watching) {
    watching = true
    watch(
      () => AccountService._loggedIn.value,
      (loggedIn) => {
        if (loggedIn) void refresh()
        else count.value = 0
      },
      { immediate: true }
    )
  }
  return { count: computed(() => count.value), refresh, set }
}
