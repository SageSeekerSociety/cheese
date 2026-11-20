// 待办页「等你处理」那份清单：读、重读、就地处理。
//
// 清单本身由后端按当下的状态现算（`/awaiting-me`），所以这里列的就是角标上数的那几件。
// 画它的组件（`components/common/Notification/WaitingList.vue`）只收这里的状态、把点击
// 交回来——组件不碰 API 层（.claude/rules/architecture.md）。
import type { WaitingAsk, WaitingItem, WaitingRun, WaitingWork } from '@/cx_types'

import { onScopeDispose, ref } from 'vue'

import { useProjectInvitationAnswer } from '@/composables/useProjectInvitationAnswer'
import { useProjectTransferAnswer } from '@/composables/useProjectTransferAnswer'
import { useTeamInvitationAnswer } from '@/composables/useTeamInvitationAnswer'
import { useTeamJoinRequestAnswer } from '@/composables/useTeamJoinRequestAnswer'

import { decideMcpRun, listAwaitingMe, markAllAlertsRead, markRead, resolveAlert } from '@/api'
import { t } from '@/i18n'
import { onInboxChange } from '@/lib/inboxLive'
import { waitingKey } from '@/lib/waitingKey'
import { keys } from '@/query/keys'
import { fromSnapshot } from '@/query/snapshot'
import { useWorkspaceStore } from '@/stores/workspace'

export function useWaitingList() {
  const items = ref<WaitingItem[]>([])
  const loading = ref(true)
  const failed = ref(false)
  const errorReason = ref('')

  // 换掉整张清单的读：只认最后发出去的那一次，早发的晚到就丢掉。
  let latest = 0
  async function load(quiet = false) {
    const mine = ++latest
    if (!quiet) loading.value = true
    failed.value = false
    errorReason.value = ''
    try {
      const payload = quiet ? await listAwaitingMe() : await fromSnapshot(keys.awaitingMe(), () => listAwaitingMe())
      const rows = payload.data
      if (mine === latest) items.value = rows
    } catch (e) {
      if (mine !== latest) return
      // 后台重读失败不把看得好好的清单换成报错：下一次再说。
      if (!quiet) {
        failed.value = true
        errorReason.value = e instanceof Error ? e.message : ''
      }
    } finally {
      if (mine === latest) loading.value = false
    }
  }

  // 收件箱多了一行，多半是有一件事刚落到你手上（一道提问、一份待审阅、一条邀请）：那
  // 一行只负责把你叫回来，事情本身在这份清单里，所以当场重读。
  onScopeDispose(onInboxChange(() => void load(true)))

  // 拍板、标为已读：交上去，这一件随之从清单上消失。
  const acting = ref<number | null>(null)
  const actionError = ref('')
  async function settle(item: WaitingWork, send: (alertId: number) => Promise<unknown>) {
    if (item.alertId == null) return
    acting.value = item.alertId
    actionError.value = ''
    try {
      await send(item.alertId)
      items.value = items.value.filter((other) => other.kind !== 'work' || other.alertId !== item.alertId)
    } catch (e) {
      actionError.value = e instanceof Error ? e.message : t('home.inbox.actionFailed')
    } finally {
      acting.value = null
    }
  }
  const decide = (item: WaitingWork, chosen: string) => settle(item, (id) => resolveAlert(id, chosen))
  const dismiss = (item: WaitingWork) => settle(item, markRead)

  // 一个项目里攒了好几条变更提醒时，一下全部标为已读，不用一条一条点。
  const clearing = ref<string | null>(null)
  async function dismissAll(projectId: string) {
    clearing.value = projectId
    actionError.value = ''
    try {
      await markAllAlertsRead(projectId)
      items.value = items.value.filter(
        (item) => item.kind !== 'work' || item.projectId !== projectId || item.reason !== 'read'
      )
    } catch (e) {
      actionError.value = e instanceof Error ? e.message : t('home.inbox.actionFailed')
    } finally {
      clearing.value = null
    }
  }

  // 回答一条邀请或申请：成了就从清单上拿掉；没成，提示由各自的 composable 弹，行留着。
  const store = useWorkspaceStore()
  const projectInvitation = useProjectInvitationAnswer()
  const projectTransfer = useProjectTransferAnswer()
  const teamInvitation = useTeamInvitationAnswer()
  const joinRequest = useTeamJoinRequestAnswer()
  const replying = ref<string | null>(null)
  async function reply(item: WaitingAsk, yes: boolean) {
    const key = waitingKey(item)
    replying.value = key
    try {
      const done =
        item.kind === 'project_invite'
          ? await projectInvitation.answer(item.id, yes)
          : item.kind === 'project_transfer'
            ? await projectTransfer.answer(item.id, yes)
            : item.kind === 'team_invite'
              ? await teamInvitation.answer(item.id, yes)
              : await joinRequest.answer(item.id, Number(item.placeId), yes)
      if (!done) return
      items.value = items.value.filter((other) => waitingKey(other) !== key)
      // 进了一个项目，左边那一列要多出它来。
      if (yes && (item.kind === 'project_invite' || item.kind === 'project_transfer')) void store.refreshProjects()
    } finally {
      replying.value = null
    }
  }

  // 允许或不允许在自己电脑上运行一条命令：答了就从清单上拿掉；没成，话说在清单上方。
  async function allow(item: WaitingRun, yes: boolean) {
    const key = waitingKey(item)
    replying.value = key
    actionError.value = ''
    try {
      await decideMcpRun(item.id, yes)
      items.value = items.value.filter((other) => waitingKey(other) !== key)
    } catch (e) {
      actionError.value = e instanceof Error ? e.message : t('home.inbox.actionFailed')
    } finally {
      replying.value = null
    }
  }

  return {
    items,
    loading,
    failed,
    errorReason,
    actionError,
    acting,
    clearing,
    replying,
    load,
    decide,
    dismiss,
    dismissAll,
    reply,
    allow,
  }
}
