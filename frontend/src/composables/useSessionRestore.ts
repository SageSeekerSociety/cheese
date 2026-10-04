// 冷打开时「正在恢复登录状态 / 连不上、可以重试」那一层的逻辑。
//
// 访问令牌只活 15 分钟，所以回访的人几乎每次冷打开都要先续签一次（一次网络往返）。
// 弱网下这段时间屏幕上以前什么都没有（外壳的内容区被藏起来等首屏路由，而首屏路由
// 又在等这次确认），确认失败更糟：会话明明还好好的，人被当成生人送去推广页，也没
// 有任何重试。见 services/account.ts 的 RestorePhase。
//
// 逻辑放在这里（而不是组件里）有两个原因：视图那一层才认得路由和账号服务——组件
// 边界规则不允许 src/components 下的文件碰 services/router（见
// scripts/import-boundary-ratchet-core.mjs）；以及这段「等多久才露面、网络回来怎么
// 重试」的决定本来就不该和一段模板绑死。
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import router from '@/router'
import { landingForMember } from '@/router/home'
import AccountService from '@/services/account'

// 展示层（SessionRestoreGate）也要用这个类型，但从组件里 import services 会被组件
// 边界规则拦下——从这里替它转出去，类型的出处仍然只有 services/account.ts 一处。
export type { RestorePhase } from '@/services/account'

// 恢复得快（正常的冷打开、网好）时不要让这一层闪一下。
const SHOW_DELAY_MS = 300

export function useSessionRestore() {
  const phase = computed(() => AccountService.restorePhase)
  const retrying = ref(false)
  const navigationFailed = ref(false)

  const visible = ref(false)
  let showTimer: ReturnType<typeof setTimeout> | undefined

  watch(
    phase,
    (current) => {
      clearTimeout(showTimer)
      if (current === 'idle') {
        visible.value = false
        return
      }
      if (visible.value) return
      showTimer = setTimeout(() => {
        visible.value = phase.value !== 'idle'
      }, SHOW_DELAY_MS)
    },
    { immediate: true }
  )
  onBeforeUnmount(() => clearTimeout(showTimer))

  async function retry() {
    if (retrying.value) return
    retrying.value = true
    navigationFailed.value = false
    try {
      if (await AccountService.retryRestore()) await goHome()
    } finally {
      retrying.value = false
    }
  }

  // 恢复成功后落回上次待的地方。首屏那次导航早就带着「没登录」的结论结束了（人当
  // 时确实还没被确认），所以这里要把「登录着该去哪」那份决定重走一遍。
  async function goHome() {
    try {
      await router.replace(await landingForMember())
    } catch {
      // 路由这一下失败说明网络还是不稳；那一层留在原地，重试按钮还在。
      navigationFailed.value = true
    }
  }

  function continueAsGuest() {
    AccountService.dismissRestore()
  }

  // 「网络回来就自己转出来」：浏览器告诉我们有网了，或者人回到这个标签页时，各再
  // 试一次。不做定时轮询——那会在安静的后台标签页里一直烧请求。
  function retryWhenBack() {
    if (document.visibilityState !== 'visible') return
    if (phase.value !== 'unreachable') return
    void retry()
  }
  onMounted(() => {
    window.addEventListener('online', retryWhenBack)
    document.addEventListener('visibilitychange', retryWhenBack)
  })
  onBeforeUnmount(() => {
    window.removeEventListener('online', retryWhenBack)
    document.removeEventListener('visibilitychange', retryWhenBack)
  })

  return { visible, phase, retrying, navigationFailed, retry, continueAsGuest }
}
