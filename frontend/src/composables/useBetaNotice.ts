// 「知是正在内测」：每个账号打开应用时说一次，内测期间有哪些额度可拿——每月方案、
// 内测体验额度、反馈被采纳、邀请好友。还没领体验额度的，主按钮就是领取。
//
// 记「说过了」和第一次采纳后那张邀请小卡（`useInvitePrompt.ts`）在同一个地方：这台
// 浏览器里按账号记一条。平台没有按人存这类小标记的地方，为一次性的说明新开一张表不值；
// 代价是换一台浏览器会再说一次。
//
// 一出来就记成说过了：刷新页面不会再出来。新手引导在带、协议还没同意、邀请小卡开着的
// 时候都先等着，几个浮层不叠在一起。
import type { BetaCredits } from '@/types/creditOffers'

import { computed, ref, watch } from 'vue'

import { claimBetaCredits, getBetaCredits } from '@/api/creditOffers'
import { t } from '@/i18n'
import { storageGet, storageSet } from '@/lib/browserStorage'
import { toast } from '@/lib/toast'
import { myHandle } from '@/me'

const key = (handle: string) => `cheese.betaNotice.${handle}`

export function useBetaNotice(blocked: () => boolean) {
  const open = ref(false)
  const offer = ref<BetaCredits | null>(null)
  const claiming = ref(false)
  const claimFailed = ref(false)
  /** 这台浏览器上这个账号已经说过了（或者这一次正在说）。 */
  const told = ref<string | null>(null)

  const due = computed(() => {
    const handle = myHandle()
    if (!handle || blocked()) return false
    return told.value !== handle && storageGet(key(handle)) === null
  })

  watch(
    due,
    async (now) => {
      const handle = myHandle()
      if (!now || !handle) return
      told.value = handle
      // 数要先拿到才出来：一张空表或者写错的数比不说更糟。拿不到这一次就不说，也不记
      // 说过了：下次打开应用再试（这一次不重试，免得接口坏着时一直打）。
      const data = await getBetaCredits().catch(() => null)
      if (!data || myHandle() !== handle) return
      offer.value = data
      // 存不下：这一次照样算数，下次进来可能再说一回。
      storageSet(key(handle), 'seen')
      open.value = true
    },
    { immediate: true }
  )

  function dismiss() {
    open.value = false
  }

  /** 领内测体验额度。领到了（或者早就领过）就关上；没领到留着，让他再点一次。 */
  async function claim(): Promise<boolean> {
    if (claiming.value) return false
    claiming.value = true
    claimFailed.value = false
    try {
      const { credits } = await claimBetaCredits()
      open.value = false
      toast(t('account.betaNotice.claimed', { n: credits }))
      return true
    } catch {
      claimFailed.value = true
      return false
    } finally {
      claiming.value = false
    }
  }

  return { open, offer, claiming, claimFailed, dismiss, claim }
}
