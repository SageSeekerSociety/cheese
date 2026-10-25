<script setup lang="ts">
// 团队设置的「成员」（`/teams/:handle/members`）。这一页原来 865 行，装了三件事：取数与那一堆
// 写操作、「谁在管这个团队」的判断、以及列表的画法。现在画法全在同目录的 `MembersView.vue`，
// 这一层只剩**接线** —— 哪一件点哪一下、递什么上去，看下面那个模板就够了：
//
//   1. **读**。进来取一次成员，能带人进来的顺手取加入申请和已发送的邀请（有才画那两张卡）；
//      收件箱来新通知或窗口回到前台时在后台重读。
//   2. **写**。升管理员 / 降成员 / 移出、批准 / 拒绝、撤回邀请、邀请某人，都在这层。
//   3. **地址**。`?tab=` / `?applicationId=` 读出要滚到哪一张卡，`?invite=1` 打开邀请框。自己名下只有
//      自己，没有这一页（侧栏里也不列），直接输地址进来的送回它的项目。
//   4. **加入方式那张卡**（`TeamJoinLinkCard`）管的几样：团队链接、加入要不要审批、这个
//      团队在搜索里露不露面（团队地址在「基本信息」）。取数也在这层，卡只吃数据、往上报动作，从下面那个
//      具名插槽递进画面 —— 画面那一半因此一行取数都没有。
defineOptions({ name: 'Members' })

import type { Team, TeamMember, TeamMembershipApplication, TeamVisibility } from '@/types'

import { computed, inject, onMounted, onScopeDispose, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { toast } from 'vuetify-sonner'
import { useEventListener } from '@vueuse/core'

import { useAccountLookup } from '@/composables/useAccountLookup'

import MembersView from './MembersView.vue'
import TeamJoinLinkCard from './TeamJoinLinkCard.vue'

import { ApiError } from '@/api'
import { TeamsApi } from '@/api/teams'
import { t } from '@/i18n'
import { teamDataInjectionKey } from '@/keys'
import { onInboxChange } from '@/lib/inboxLive'
import { useDialog } from '@/plugins/dialog'
import AccountService from '@/services/account'
import errorHandler from '@/services/ErrorHandler'

const route = useRoute()
const isInviteDialogActive = ref(false)
const teamMembers = ref<TeamMember[]>([])
// 读失败和「还没有成员」是两件事：失败替换掉这一格的内容，空状态才说「暂无」。
const failedMembers = ref(false)
const membersError = ref<string | null>(null)
const teamData = inject(teamDataInjectionKey, ref())
const dialog = useDialog()

const activeTab = ref('members')

const isSelfOwner = computed(() => {
  if (!teamData.value) {
    return false
  }
  return AccountService.user?.id === teamData.value.owner.id
})

// 看服务端给的 role，不看 admins.examples：那份名单最多只有 3 个人，第 4 个管理员会被当成普通成员。
const isSelfAdmin = computed(() => teamData.value?.role === 'OWNER' || teamData.value?.role === 'ADMIN')

// 邀请、加入链接、加入申请：把人带进团队的几样。移出成员不在其内：那是往外走，不是往里进。
const canBringPeopleIn = computed(() => isSelfAdmin.value && !teamData.value?.personal)

// 自己名下只有自己，没有成员这一页（侧栏里也不列）：直接输地址进来的，带回它的项目。
const router = useRouter()
watch(
  () => teamData.value?.personal,
  (own) => {
    if (own && teamData.value)
      void router.replace({ name: 'TeamsDetailDefault', params: { handle: teamData.value.handle } })
  },
  { immediate: true }
)

const updateActiveTabFromRoute = () => {
  if (route.query.tab && ['members', 'requests', 'invitations'].includes(route.query.tab as string)) {
    activeTab.value = route.query.tab as string
  }

  if (route.query.applicationId) {
    if (route.query.type === 'invitation') {
      activeTab.value = 'invitations'
    } else {
      activeTab.value = 'requests'
    }
  }

  // 首页侧栏团队那一行的「邀请成员」带着 ?invite=1 过来：直接打开邀请框。
  if (route.query.invite === '1' && canBringPeopleIn.value) isInviteDialogActive.value = true
}

// 按完整的用户名或邮箱找人，邀请查到的那一位。以前这里是一格 UID：没人知道别人的
// UID，打进去的用户名原样当 userId 发出去，被参数校验挡回来；以数字开头的用户名还会
// 被截成一个数，请到另一个人。
const {
  query: inviteQuery,
  found,
  lookingUp,
  lookupError,
  reset: resetInviteLookup,
} = useAccountLookup((e) =>
  e instanceof ApiError && e.status === 404
    ? t('teams.members.inviteNotFound')
    : e instanceof Error && e.message
      ? e.message
      : t('teams.members.inviteLookupFailed')
)
// 已经在团队里的人不用再邀请——后端也会拒，但那是按下按钮之后才知道。项目的
// 「邀请外部成员」是同一条。
const alreadyIn = computed(() => !!found.value && teamMembers.value.some((m) => m.user.id === found.value?.id))
const inviteRoleInput = ref('MEMBER')
const inviteMessageInput = ref('')

const joinRequests = ref<TeamMembershipApplication[]>([])
// 正在批准或拒绝的那条申请：回话之前两个按钮都按不动，连点只发一次。
const answering = ref<number>()
const loadingRequests = ref(false)
const failedRequests = ref(false)
const requestsError = ref<string | null>(null)
let requestsRead = 0
let requestsLoaded = false

const teamInvitations = ref<TeamMembershipApplication[]>([])
const loadingInvitations = ref(false)
const failedInvitations = ref(false)
const invitationsError = ref<string | null>(null)
let invitationsRead = 0
let invitationsLoaded = false

const fetchTeamMembers = async (teamId: number) => {
  failedMembers.value = false
  membersError.value = null
  try {
    const { members } = await TeamsApi.getMembers(teamId)
    teamMembers.value = members
  } catch (error) {
    console.error('Failed to load team members', error)
    failedMembers.value = true
    membersError.value = error instanceof Error && error.message ? error.message : null
  }
}

// 重试时重新拿这一格：团队 id 从注入的 teamData 来。
const retryMembers = () => {
  if (teamData.value) fetchTeamMembers(teamData.value.id)
}

// quiet：手里已经有上一份名单时，后台重读不把整页换成转圈，也不因一次瞬断把好好的列表刷成报错。
const fetchJoinRequests = async (teamId: number, quiet = false) => {
  const read = ++requestsRead
  const silent = quiet && requestsLoaded
  if (!silent) {
    loadingRequests.value = true
    failedRequests.value = false
    requestsError.value = null
  }
  try {
    const response = await TeamsApi.listTeamJoinRequests(teamId)
    if (read !== requestsRead || teamData.value?.id !== teamId || !canBringPeopleIn.value) return
    requestsLoaded = true
    joinRequests.value = response.applications
    failedRequests.value = false
    requestsError.value = null
  } catch (error) {
    if (read !== requestsRead || teamData.value?.id !== teamId) return
    console.error('Failed to load join requests', error)
    if (!silent) {
      failedRequests.value = true
      requestsError.value = error instanceof Error && error.message ? error.message : null
    }
  } finally {
    if (read === requestsRead) {
      loadingRequests.value = false
    }
  }
}

const retryRequests = () => {
  if (teamData.value) void fetchJoinRequests(teamData.value.id)
}

const fetchTeamInvitations = async (teamId: number, quiet = false) => {
  const read = ++invitationsRead
  const silent = quiet && invitationsLoaded
  if (!silent) {
    loadingInvitations.value = true
    failedInvitations.value = false
    invitationsError.value = null
  }
  try {
    const response = await TeamsApi.listTeamInvitations(teamId)
    if (read !== invitationsRead || teamData.value?.id !== teamId || !canBringPeopleIn.value) return
    invitationsLoaded = true
    teamInvitations.value = response.invitations
    failedInvitations.value = false
    invitationsError.value = null
  } catch (error) {
    if (read !== invitationsRead || teamData.value?.id !== teamId) return
    console.error('Failed to load sent invitations', error)
    if (!silent) {
      failedInvitations.value = true
      invitationsError.value = error instanceof Error && error.message ? error.message : null
    }
  } finally {
    if (read === invitationsRead) {
      loadingInvitations.value = false
    }
  }
}

const retryInvitations = () => {
  if (teamData.value) void fetchTeamInvitations(teamData.value.id)
}

onMounted(() => {
  const teamId = teamData.value!.id
  void fetchTeamMembers(teamId)

  updateActiveTabFromRoute()
})
watch(() => route.query.invite, updateActiveTabFromRoute)

watch(
  () => route.query,
  () => {
    updateActiveTabFromRoute()
  },
  { immediate: true }
)

watch(activeTab, (newTab) => {
  const teamId = teamData.value?.id
  if (teamId === undefined || !canBringPeopleIn.value) return

  if (newTab === 'requests') {
    void fetchJoinRequests(teamId, true)
  } else if (newTab === 'invitations') {
    void fetchTeamInvitations(teamId, true)
  }
})

// 有人递来新的加入申请（或回掉了发出去的邀请），服务端会经收件箱连接推一声
// （`lib/inboxLive.ts`）：当场在后台重读，页签上的数字和列表不用刷新页面就跟上。
// 连接断开期间漏掉的、或别的管理员刚审掉的，切回窗口时补读一次。
const refreshLive = () => {
  const teamId = teamData.value?.id
  if (teamId === undefined || !canBringPeopleIn.value) return
  void fetchJoinRequests(teamId, true)
  void fetchTeamInvitations(teamId, true)
}
onScopeDispose(onInboxChange(refreshLive))
useEventListener(window, 'focus', refreshLive)
useEventListener(document, 'visibilitychange', () => {
  if (document.visibilityState === 'visible') refreshLive()
})

// 下面几个操作成功时有的回 204，响应体是空串：失败只认 withErrorHandling 给的 undefined，
// 拿真假判断会把成功当失败——不提示、不刷新，那一行还挂着，再点一次就是「找不到」。
const confirmInvite = async () => {
  const invitee = found.value
  if (!teamData.value || !invitee || alreadyIn.value) {
    return
  }

  const result = await errorHandler.withErrorHandling(async () => {
    return await TeamsApi.createInvitation(teamData.value!.id, {
      userId: invitee.id,
      role: inviteRoleInput.value as any,
      message: inviteMessageInput.value || undefined,
    })
  })

  if (result !== undefined) {
    toast.success(t('teams.members.inviteSent'))
    resetInviteLookup()
    inviteRoleInput.value = 'MEMBER'
    inviteMessageInput.value = ''
    isInviteDialogActive.value = false
    await fetchTeamInvitations(teamData.value!.id, true)
  }
}

const promoteToAdmin = async (userId: number) => {
  if (!teamData.value) {
    return
  }

  const result = await errorHandler.withErrorHandling(
    async () => {
      return await TeamsApi.updateMember(teamData.value!.id, userId, { role: 'ADMIN' })
    },
    { defaultMessage: t('teams.members.promoteFailed') }
  )

  if (result !== undefined) {
    toast.success(t('teams.members.promoteDone'))
    await fetchTeamMembers(teamData.value!.id)
  }
}

const demoteToMember = async (userId: number) => {
  if (!teamData.value) {
    return
  }

  const result = await errorHandler.withErrorHandling(
    async () => {
      return await TeamsApi.updateMember(teamData.value!.id, userId, { role: 'MEMBER' })
    },
    { defaultMessage: t('teams.members.demoteFailed') }
  )

  if (result !== undefined) {
    toast.success(t('teams.members.demoteDone'))
    await fetchTeamMembers(teamData.value!.id)
  }
}

const removeMember = async (userId: number) => {
  if (!teamData.value) {
    return
  }

  // 移出后他不再能进这个团队，先确认（§3.7）：行里的入口是灰的，红只出现在这一下确认上。
  const name = teamMembers.value.find((member) => member.user.id === userId)?.user.nickname ?? ''
  const confirmed = await dialog
    .confirm(t('teams.members.removeBody'), {
      title: t('teams.members.removeTitle', { name }),
      confirmLabel: t('teams.members.remove'),
      danger: true,
    })
    .wait()
    .catch(() => false)
  if (!confirmed) {
    return
  }

  const result = await errorHandler.withErrorHandling(
    async () => {
      return await TeamsApi.removeMember(teamData.value!.id, userId)
    },
    { defaultMessage: t('teams.members.removeFailed') }
  )

  if (result !== undefined) {
    toast.success(t('teams.members.removeDone'))
    await fetchTeamMembers(teamData.value!.id)
  }
}

const approveRequest = async (requestId: number) => {
  if (!teamData.value || answering.value !== undefined) return

  answering.value = requestId
  const result = await errorHandler.withErrorHandling(
    async () => {
      return await TeamsApi.approveJoinRequest(teamData.value!.id, requestId)
    },
    { defaultMessage: t('teams.members.approveFailed') }
  )

  if (result !== undefined) {
    toast.success(t('teams.members.approveDone'))
    // 刷新数据
    await Promise.all([fetchJoinRequests(teamData.value!.id, true), fetchTeamMembers(teamData.value!.id)])
  }
  answering.value = undefined
}

const rejectRequest = async (requestId: number) => {
  if (!teamData.value || answering.value !== undefined) return

  answering.value = requestId
  const result = await errorHandler.withErrorHandling(
    async () => {
      return await TeamsApi.rejectJoinRequest(teamData.value!.id, requestId)
    },
    { defaultMessage: t('teams.members.rejectFailed') }
  )

  if (result !== undefined) {
    toast.success(t('teams.members.rejectDone'))
    // 刷新数据
    await fetchJoinRequests(teamData.value!.id, true)
  }
  answering.value = undefined
}

const cancelInvitation = async (invitationId: number) => {
  if (!teamData.value) return

  const result = await errorHandler.withErrorHandling(
    async () => {
      return await TeamsApi.cancelInvitation(teamData.value!.id, invitationId)
    },
    { defaultMessage: t('teams.members.cancelFailed') }
  )

  if (result !== undefined) {
    toast.success(t('teams.members.cancelDone'))
    // 刷新数据
    await fetchTeamInvitations(teamData.value!.id, true)
  }
}

/** 加入链接那张卡改过这个团队（换了地址、改了可见性）：注入的那一份跟着更新。 */
const onTeamUpdated = (team: Team) => {
  teamData.value = team
}

// ---- 加入链接那张卡：它只吃数据、往上报动作，取数都在这层 ----

const joinLink = ref<TeamsApi.TeamJoinLink | null>(null)
const joinBusy = ref(false)
const joinError = ref('')
const joinCopied = ref(false)

// 这几手共用一个「在做」和一个「上一次没做成」：卡上的按钮和开关是一组，同一时刻只该有一件在做。
const runJoinLink = async (action: () => Promise<void>) => {
  joinBusy.value = true
  joinError.value = ''
  try {
    await action()
  } catch {
    joinError.value = t('work.teamLink.failed')
  } finally {
    joinBusy.value = false
  }
}

// 换一个团队就是换一份链接：上一队的先放下，别让它在下一队闪一下——上一次的报错也一样，
// 409 之后换到另一队那条「已被占用」不该还挂在卡上。取数还看权限：普通成员读这条链接后端
// 只回 403，卡在画面上是 v-if 挡的，请求也得一并挡住，不然一打开页面就发一个注定失败的那一下；
// 权限被给到（升管理员）或被收回（降回成员）时，同一条跟着走：给到才读，收回就把链接放下。
watch(
  [() => teamData.value?.id, canBringPeopleIn],
  ([teamId, allowed]) => {
    joinLink.value = null
    joinCopied.value = false
    joinError.value = ''
    requestsRead += 1
    requestsLoaded = false
    joinRequests.value = []
    loadingRequests.value = false
    failedRequests.value = false
    requestsError.value = null
    invitationsRead += 1
    invitationsLoaded = false
    teamInvitations.value = []
    loadingInvitations.value = false
    failedInvitations.value = false
    invitationsError.value = null
    if (teamId === undefined || !allowed) {
      return
    }
    // 申请和邀请有才画卡，所以进来就问；普通成员没这两张卡，也不发那个必回 403 的请求。
    void fetchJoinRequests(teamId)
    void fetchTeamInvitations(teamId)
    void runJoinLink(async () => {
      const data = await TeamsApi.getJoinLink(teamId)
      // 换队之前的回包不算数：它属于上一个团队。
      if (teamData.value?.id === teamId) {
        joinLink.value = data
      }
    })
  },
  { immediate: true }
)

const resetJoinLink = () => {
  if (!teamData.value) {
    return
  }
  joinCopied.value = false
  void runJoinLink(async () => {
    joinLink.value = await TeamsApi.resetJoinLink(teamData.value!.id)
  })
}

const setJoinApproval = (approval: boolean) => {
  if (!teamData.value) {
    return
  }
  void runJoinLink(async () => {
    joinLink.value = await TeamsApi.updateJoinLink(teamData.value!.id, { approval })
  })
}

const setJoinVisibility = (visibility: TeamVisibility) => {
  if (!teamData.value) {
    return
  }
  void runJoinLink(async () => {
    const { team } = await TeamsApi.update(teamData.value!.id, { visibility })
    onTeamUpdated(team)
  })
}

// 复制用的是浏览器那一手：不给权限、不是安全上下文就会失败，照实说一句。
const copyJoinLink = async (url: string) => {
  joinError.value = ''
  try {
    await navigator.clipboard.writeText(url)
    joinCopied.value = true
  } catch {
    joinError.value = t('work.teamLink.copyFailed')
  }
}
</script>

<template>
  <MembersView
    v-model:active-tab="activeTab"
    v-model:is-invite-dialog-active="isInviteDialogActive"
    v-model:invite-query="inviteQuery"
    v-model:invite-role-input="inviteRoleInput"
    v-model:invite-message-input="inviteMessageInput"
    :is-self-owner="isSelfOwner"
    :can-bring-people-in="canBringPeopleIn"
    :already-in="alreadyIn"
    :members="teamMembers"
    :failed-members="failedMembers"
    :members-error="membersError"
    :join-requests="joinRequests"
    :loading-requests="loadingRequests"
    :failed-requests="failedRequests"
    :requests-error="requestsError"
    :answering="answering"
    :invitations="teamInvitations"
    :loading-invitations="loadingInvitations"
    :failed-invitations="failedInvitations"
    :invitations-error="invitationsError"
    :found="found"
    :looking-up="lookingUp"
    :lookup-error="lookupError"
    @retry-members="retryMembers"
    @promote="promoteToAdmin"
    @demote="demoteToMember"
    @remove-member="removeMember"
    @retry-requests="retryRequests"
    @approve-request="approveRequest"
    @reject-request="rejectRequest"
    @retry-invitations="retryInvitations"
    @cancel-invitation="cancelInvitation"
    @confirm-invite="confirmInvite"
  >
    <template #join-link>
      <TeamJoinLinkCard
        v-if="teamData && canBringPeopleIn"
        :team="teamData"
        :link="joinLink"
        :busy="joinBusy"
        :error="joinError"
        :copied="joinCopied"
        @reset="resetJoinLink"
        @set-approval="setJoinApproval"
        @set-visibility="setJoinVisibility"
        @copy="copyJoinLink"
      />
    </template>
  </MembersView>
</template>
