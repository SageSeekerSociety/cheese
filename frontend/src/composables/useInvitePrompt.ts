// 第一次采纳之后，提醒一次「可以邀请好友了」。那一刻他刚觉得芝士好用，邀请码也正好多了
// 3 个名额（后端 `referrals.on_accepted`）。
//
// 「第一次」靠的是采纳前后邀请码解锁没解锁：有一张等他采纳的卡时先问一次自己的邀请码
// （`prime`），那时还没解锁，采纳成功（`noteAccepted`）就是第一次。问的时候已经解锁，
// 说明他早就采纳过，这辈子不再提醒。
//
// 每个账号在这台浏览器上只提醒一次（和新手引导的「走完了」同一个地方，见
// `useProjectTour.ts`）。到期了但新手引导还在带，就等引导走完再出来，两个浮窗不抢。
import type { MyInvites } from '@/types/invites'

import { computed, ref, watch } from 'vue'

import { getMyInvites, inviteSignupLink } from '@/api/invites'
import { copyLink } from '@/commands/copy'
import { storageGet, storageSet } from '@/lib/browserStorage'
import { myHandle } from '@/me'

const key = (handle: string) => `cheese.invitePrompt.${handle}`

/** 'due'：该提醒了，还没出来过；'seen'：提醒过，或者用不着提醒。 */
type Mark = 'due' | 'seen'

const mark = ref<Mark | null>(null)
/** `mark` 读的是哪个账号的；换了人就重读。 */
let markFor = ''
/** 问到邀请码时还没解锁：这一次采纳就是第一次。 */
let armed = false
let priming: Promise<void> | null = null
const invites = ref<MyInvites | null>(null)

function readMark(handle: string): Mark | null {
  if (markFor !== handle) {
    markFor = handle
    const stored = storageGet(key(handle))
    mark.value = stored === 'due' || stored === 'seen' ? stored : null
    armed = false
    priming = null
  }
  return mark.value
}

function writeMark(handle: string, value: Mark) {
  mark.value = value
  // 存不下：这一次照样算数，下次进来可能再提醒一回。
  storageSet(key(handle), value)
}

/** 有一张等他采纳的卡：趁还没采纳，看一眼邀请码解锁了没有。 */
export function primeInvitePrompt(): Promise<void> {
  const handle = myHandle()
  if (!handle || readMark(handle) !== null) return Promise.resolve()
  priming ??= getMyInvites()
    .then((mine) => {
      if (markFor !== handle) return
      if (mine.unlocked) writeMark(handle, 'seen')
      else armed = true
    })
    .catch(() => {
      // 问不到就不提醒：宁可漏一次，不在采纳过的人面前说「第一次」。
      priming = null
    })
  return priming
}

/** 他刚采纳了一张卡。 */
export async function noteAccepted(): Promise<void> {
  const handle = myHandle()
  if (!handle || readMark(handle) !== null) return
  await priming
  if (!armed || markFor !== handle) return
  armed = false
  writeMark(handle, 'due')
}

/** 给测试用：像重新打开页面那样，从这台浏览器存下的状态重新读。 */
export function __resetInvitePromptForTests() {
  mark.value = null
  markFor = ''
  armed = false
  priming = null
  invites.value = null
}

export function useInvitePrompt(blocked: () => boolean) {
  const showing = ref(false)
  const due = computed(() => {
    const handle = myHandle()
    return !!handle && readMark(handle) === 'due' && !blocked()
  })

  // 出来了就记成提醒过：刷新页面不会再出来一次。
  watch(
    due,
    async (now) => {
      const handle = myHandle()
      if (!now || !handle) return
      showing.value = true
      writeMark(handle, 'seen')
      invites.value = await getMyInvites().catch(() => null)
    },
    { immediate: true }
  )

  function dismiss() {
    showing.value = false
  }

  async function copySignupLink() {
    const mine = invites.value ?? (await getMyInvites().catch(() => null))
    if (!mine) return
    await copyLink(inviteSignupLink(mine.code))
    showing.value = false
  }

  return { open: showing, invites, dismiss, copySignupLink }
}
