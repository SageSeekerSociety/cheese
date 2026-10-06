<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { useAdminSections } from '@/composables/useAdminSections'

import { useFeedbackStore } from '@/stores/feedback'
import AdminLayoutView from '@/views/admin/AdminLayoutView.vue'

// 管理后台的内容区（`/admin/*` 的默认视图）。分区在侧栏（`AdminSidebar`），这一层管的是
// 门、全局那几颗键和当前那一块。
//
// 门的三个状态**画在这里一次**：
//
// - meta 还在路上 → 「正在确认权限…」。少了这一档，一个真管理员打开页面看到的第一句话
//   是「你的账号不在管理员名单里」—— 一句假话，比一张空表更难查。
// - 不是管理员 → 一句话加回去的路。不是白屏、不是 404、不是一个空列表：这三种在界面上
//   长得像「后台里没东西」，而真相是「你没在名单里」。
// - 是管理员 → `<RouterView>` 画子页。
//
// 于是子页**不需要**再问一次「我是不是管理员」：子页只要被画出来，就一定过了这道门。
// 各块自己的接口后面还会各自判一次（服务端不信客户端），那是服务端的事。
//
// 进了后台却落在一块自己进不去的分区上（`/admin` 默认去队列），会换到第一块能进的 ——
// 那件事现在在**路由**里（`router/feedback.ts` 里 `/admin` 的 `beforeEnter`），不藏在这
// 一层的 `watch` 里：静悄悄改地址看不出是「按权限改道」，声明式的重定向读得到。
defineOptions({ name: 'AdminLayout' })

const store = useFeedbackStore()
const route = useRoute()
const router = useRouter()
const { canEnter } = useAdminSections()

/** `?` 那一层（§8）。`Esc` 关闭由 Vuetify 的对话框自己管。 */
const shortcutOpen = ref(false)

/** `G` 之后那一颗（§8 的序列键）。1s 内有效，超时就算没按过 —— 不然「按了 G 去泡咖啡、
 *  回来顺手按了个 D」会把人送去看板。 */
let gPressedAt = 0
const SEQUENCE_MS = 1000

function isTyping(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null
  if (!el || !el.tagName) return false
  return el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT' || el.isContentEditable
}

function refreshCurrent() {
  // `R` 是全局键（§8），而「当前这一页该重拉什么」只有路由知道。
  if (route.name === 'AdminDashboard') void store.loadStats()
  else if (route.name === 'AdminQueue' || route.name === 'AdminFeedback') void store.loadAdmin()
}

function onKeydown(event: KeyboardEvent) {
  // 带修饰键的一律放行：那是浏览器/系统的快捷键（`Cmd+F`、`Ctrl+K`），这一层不该抢。
  if (event.metaKey || event.ctrlKey || event.altKey) return
  if (isTyping(event.target)) return

  if (event.key === '?') {
    event.preventDefault()
    shortcutOpen.value = true
    return
  }
  if (event.key === 'r' || event.key === 'R') {
    event.preventDefault()
    refreshCurrent()
    return
  }

  if (event.key === 'g' || event.key === 'G') {
    gPressedAt = Date.now()
    return
  }

  // 序列键的第二颗。不在窗口里就当作普通按键放走 —— 不能 `preventDefault`，不然在
  //  没按 `G` 的时候按 `q` 会变成一个什么都不做的黑洞。
  if (Date.now() - gPressedAt >= SEQUENCE_MS) return
  gPressedAt = 0
  const key = event.key.toLowerCase()
  if (key === 'q') {
    event.preventDefault()
    void router.push('/admin/queue')
  } else if (key === 'd') {
    event.preventDefault()
    void router.push('/admin/dashboard')
  } else if (key === 'f') {
    event.preventDefault()
    void router.push('/feedback')
  }
}

onMounted(() => {
  // meta 是门画哪一档的依据。它可能已经被用户侧拉过了，再调一次是幂等的 —— 而直接输
  // 地址进来的时候没有它就没法判断。
  void store.loadMeta().then(() => {
    // 未读数挂在侧栏上，而旁边的子页不一定是队列（成员页不拉 counts）。所以这一层自己
    // 问一次 —— 少了它，在成员页上那个数永远停在 0，看着像「没有未读」。
    if (store.isAdmin) void store.refreshCounts()
  })
  window.addEventListener('keydown', onKeydown)
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeydown)
})
</script>

<template>
  <AdminLayoutView v-model:shortcut-open="shortcutOpen" :meta-checked="store.metaChecked" :can-enter="canEnter" />
</template>
