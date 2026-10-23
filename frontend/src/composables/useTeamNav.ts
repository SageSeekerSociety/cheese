// 首页那一格目录的判断：取数、写回、盯路由。
//
// `views/home/HomeNav.vue` 只吃 props 出事件（A 档），四个弹窗也只吃 props 出事件；
// 真去问后端、真去跳地址、真去确认「退不退得掉」的都在这一层。两端（桌面侧栏
// `HomeSidebar`、手机整页 `HomeHub`）各挂一份目录，判断只有这一份。
import type { SpaceLink, Team, TeamMember } from '@/types'

import { computed, onScopeDispose, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { toast } from 'vuetify-sonner'
import { useEventListener } from '@vueuse/core'
import { useQuery } from '@tanstack/vue-query'

import { awaitingCount } from '@/composables/useAwaitingCount'
import { useJoinSpace } from '@/composables/useJoinSpace'

import { ApiError } from '@/api'
import { createAvatar } from '@/api/avatars'
import { onAwaitingChanged } from '@/api/awaitingChanged'
import { SpacesApi } from '@/api/spaces'
import { onSpacesChanged } from '@/api/spacesChanged'
import { TeamsApi } from '@/api/teams'
import { t } from '@/i18n'
import { useDialog } from '@/plugins/dialog'
import { patchQuery, queryClient } from '@/query/client'
import { mySpacesQuery, myTeamsQuery } from '@/query/homeNav'
import { keys } from '@/query/keys'
import AccountService from '@/services/account'
import errorHandler from '@/services/ErrorHandler'
import { useWorkspaceStore } from '@/stores/workspace'

export function useTeamNav(options: { active?: () => boolean } = {}) {
  const route = useRoute()
  const router = useRouter()
  const dialog = useDialog()

  // 侧栏那一份在手机上没人看得见（同一份目录是整页的 `HomeHub`），可容器照样挂在那里：
  // 给它 `active`，只在看得见的时候取数。整页那一份不传，一直取。
  const active = options.active ?? (() => true)

  // 团队和空间都是服务器上的数据，读的是 `query/homeNav` 里那一份。从空间或项目回到
  // 首页时这块侧栏会卸载重挂，名单留在缓存里，第一帧就画得出来，不用从空开始等请求；
  // 过期了在后台再问一次，画面不闪。`enabled` 负责「手机上看得见才取数」，从窄变宽
  // 时它自己会补一次。
  const teamsRead = useQuery(
    computed(() => ({ ...myTeamsQuery(), enabled: active() })),
    queryClient
  )
  const spacesRead = useQuery(
    computed(() => ({ ...mySpacesQuery(), enabled: active() })),
    queryClient
  )
  const teams = computed<Team[]>(() => teamsRead.data.value ?? [])
  const spaces = computed<SpaceLink[]>(() => spacesRead.data.value ?? [])

  // 这里自己改过名单（退出、解散、转让、改资料）：先作废正在路上的那次读，再改缓存。
  // 那次读是改之前发出去的，晚回来会把刚退出的团队又画回去。
  async function setTeamsHere(change: (current: Team[]) => Team[]) {
    await patchQuery<Team[]>(keys.myTeams(), change)
  }

  // 名单会变，可后端不推：换页面（建完团队就是跳进那个团队）、窗口重新拿到焦点
  // （批准往往是在别处等来的）时重读；在别处改了空间或了结了一件待办，也当场重读。
  watch(
    () => route.path,
    () => {
      if (active()) void teamsRead.refetch()
    }
  )
  useEventListener(window, 'focus', () => {
    if (active()) void teamsRead.refetch()
  })
  // 在哪一页建了、删了、加入或退出了一个空间，或者改了一份申请：空间那一组当场重读。
  onScopeDispose(
    onSpacesChanged(() => {
      if (active()) void spacesRead.refetch()
    })
  )
  // 在待办里接受了一条团队邀请（或别的会了结待办的事）：名单可能多了一个团队，当场重读。
  onScopeDispose(
    onAwaitingChanged(() => {
      if (active()) void teamsRead.refetch()
    })
  )

  // 正在看某个团队的某一页时，那个团队一定是展开的：否则侧栏上找不到「你在这儿」。
  // 展开状态本身（哪几行摊开、记不记在这台浏览器上）是画面的事，留在 `HomeNav.vue`；
  // 这里只把「现在是哪个团队」交出去。
  const currentHandle = computed(() =>
    typeof route.params.handle === 'string' && route.path.startsWith('/teams/') ? route.params.handle : null
  )

  const awaiting = awaitingCount()

  // ---- 用邀请码加入空间：问后端、成了进那个空间，和待办页共用 `useJoinSpace`。
  const {
    open: joinOpen,
    busy: joinBusy,
    error: joinError,
    join: joinSpace,
  } = useJoinSpace(() => void spacesRead.refetch())

  // ---- 新建空间：提交以后就是一份待审核的申请，落到那份申请上。侧栏那一组由
  // `onSpacesChanged` 重读，这里不用管。
  const createOpen = ref(false)
  const createBusy = ref(false)
  const createError = ref('')
  watch(createOpen, (open) => {
    if (open) createError.value = ''
  })
  async function createSpace(payload: { name: string; intro: string; avatarFile?: File }) {
    const name = payload.name.trim()
    if (createBusy.value || !name) return
    createBusy.value = true
    createError.value = ''
    try {
      const avatarId = payload.avatarFile ? (await createAvatar(payload.avatarFile)).avatarId : undefined
      const { space } = await SpacesApi.create({ name, intro: payload.intro.trim(), avatarId })
      createOpen.value = false
      void router.push({ name: 'HomeSpaceApplication', params: { spaceId: String(space.id) } })
    } catch {
      createError.value = t('spaces.create.failed')
    } finally {
      createBusy.value = false
    }
  }

  // ---- 改资料。名字全站唯一，撞名要落在名字那一格，别和「没网」「没权限」混成一句。
  const editing = ref<Team | null>(null)
  const editingBusy = ref(false)
  const editingError = ref('')
  const editingNameError = ref('')

  function edit(team: Team | null) {
    editing.value = team
    clearErrors()
  }

  /** 上一次那句报错不该跟着人打字走：他在名字那一格里一动就撤掉，重新判一次。 */
  function clearErrors() {
    editingError.value = ''
    editingNameError.value = ''
  }

  async function saveProfile(payload: { name: string; intro: string; avatarFile?: File }) {
    const team = editing.value
    if (!team || editingBusy.value) return
    editingError.value = ''
    editingNameError.value = ''
    const name = payload.name.trim()
    if (!name) {
      editingNameError.value = t('work.teamProfile.nameRequired')
      return
    }
    editingBusy.value = true
    try {
      // 换头像 = 先把图传上去换一个 id，再把它和另外两项一起 PATCH 上去；
      // 没换图就不带 avatarId，免得把现成的头像覆盖掉。
      const avatarId = payload.avatarFile ? (await createAvatar(payload.avatarFile)).avatarId : null
      const { team: updated } = await TeamsApi.update(team.id, {
        name,
        intro: payload.intro.trim(),
        ...(avatarId ? { avatarId } : {}),
      })
      await setTeamsHere((rows) => rows.map((row) => (row.id === updated.id ? { ...row, ...updated } : row)))
      editing.value = null
      toast.success(t('work.teamProfile.saved'))
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) editingNameError.value = t('work.teamProfile.nameTaken')
      else if (e instanceof ApiError && e.status === 403) editingError.value = t('work.teamProfile.forbidden')
      else {
        editingError.value = t('work.teamProfile.saveFailed')
        console.error(e)
      }
    } finally {
      editingBusy.value = false
    }
  }

  // ---- 解散团队。撤不回，所以要在弹窗里把团队名打一遍；后端拒绝时理由留在弹窗里
  // （团队里还有没归档的项目），成了就和退出一样，这一行和它的项目从目录上下去。
  const disbanding = ref<Team | null>(null)
  const disbandBusy = ref(false)
  const disbandError = ref<string | null>(null)

  function openDisband(team: Team | null) {
    disbanding.value = team
    disbandError.value = null
  }

  async function confirmDisband() {
    const team = disbanding.value
    if (!team || disbandBusy.value) return
    disbandBusy.value = true
    disbandError.value = null
    try {
      await TeamsApi.del(team.id)
    } catch (e) {
      disbandError.value = e instanceof Error ? e.message : t('home.nav.disbandTeamFailed')
      disbandBusy.value = false
      return
    }
    disbandBusy.value = false
    disbanding.value = null
    toast.success(t('home.nav.disbandTeamDone', { name: team.name }))
    await forgetTeam(team)
  }

  // ---- 转让团队。所有者退不掉（后端要他先转让或解散），这里就是那条出路；交出去之后
  // 我是管理员，这一行的菜单跟着新角色长（这时才有「退出团队」）。
  const transferring = ref<Team | null>(null)
  const transferCandidates = ref<TeamMember[]>([])
  const transferLoading = ref(false)
  const transferBusy = ref(false)
  const transferError = ref<string | null>(null)

  // 这几格的状态现在活在 composable 里，弹窗卸载带不走它：关掉甲队的弹窗、再为乙队打
  // 开时，甲队那一次名单如果晚回来，会把乙队弹窗上的人换成甲队的人（读失败则把甲队的
  // 理由画成乙队的），它的 `finally` 还会在乙队名单没到时先把「还在读」翻成「还没有
  // 别的成员」。和名单一样只认最后打开的那一次。
  let transferRead = 0
  async function openTransfer(team: Team | null) {
    const read = ++transferRead
    transferring.value = team
    transferCandidates.value = []
    transferError.value = null
    transferLoading.value = false
    if (!team) return
    transferLoading.value = true
    try {
      const { members } = await TeamsApi.getMembers(team.id)
      if (read !== transferRead) return
      transferCandidates.value = members.filter((m) => m.user.id !== AccountService.user?.id)
    } catch (e) {
      if (read !== transferRead) return
      transferError.value = e instanceof Error ? e.message : t('home.nav.transferTeamLoadFailed')
    } finally {
      if (read === transferRead) transferLoading.value = false
    }
  }

  async function confirmTransfer(userId: number) {
    const team = transferring.value
    if (!team || transferBusy.value) return
    transferBusy.value = true
    transferError.value = null
    try {
      const { team: updated } = await TeamsApi.transferOwner(team.id, userId)
      await setTeamsHere((rows) =>
        rows.map((row) => (row.id === updated.id ? { ...row, ...updated, role: 'ADMIN' } : row))
      )
      transferring.value = null
    } catch (e) {
      transferError.value = e instanceof Error ? e.message : t('home.nav.transferTeamFailed')
    } finally {
      transferBusy.value = false
    }
  }

  // ---- 退出团队。退掉的是整个团队，它的项目也一起看不到了，所以先确认。退出这一下
  // 成功了就算成功，后面的刷新失败不改口。
  async function leaveTeam(team: Team) {
    const userId = AccountService.user?.id
    if (typeof userId !== 'number') return
    const confirmed = await dialog
      .confirm(t('home.nav.leaveTeamBody'), {
        title: t('home.nav.leaveTeamTitle', { name: team.name }),
        confirmLabel: t('home.nav.leaveTeam'),
        danger: true,
      })
      .wait()
      .catch(() => false)
    if (!confirmed) return
    const result = await errorHandler.withErrorHandling(() => TeamsApi.removeMember(team.id, userId), {
      defaultMessage: t('home.nav.leaveTeamFailed'),
    })
    if (result === undefined) return
    toast.success(t('home.nav.leaveTeamDone', { name: team.name }))
    await forgetTeam(team)
  }

  // 这个团队不再是我的了：这一行消失；它的项目也不再是我的，rail 上那几格跟着项目清单走；
  // 正看着它的某一页的话回待办。
  async function forgetTeam(team: Team) {
    await setTeamsHere((rows) => rows.filter((row) => row.id !== team.id))
    void useWorkspaceStore().refreshProjects()
    if (currentHandle.value?.toLowerCase() === team.handle.toLowerCase()) void router.replace({ name: 'inbox' })
  }

  return {
    teams,
    spaces,
    awaiting,
    currentHandle,
    joinOpen,
    joinBusy,
    joinError,
    joinSpace,
    createOpen,
    createBusy,
    createError,
    createSpace,
    editing,
    editingBusy,
    editingError,
    editingNameError,
    edit,
    clearErrors,
    saveProfile,
    disbanding,
    disbandBusy,
    disbandError,
    openDisband,
    confirmDisband,
    transferring,
    transferCandidates,
    transferLoading,
    transferBusy,
    transferError,
    openTransfer,
    confirmTransfer,
    leaveTeam,
  }
}
