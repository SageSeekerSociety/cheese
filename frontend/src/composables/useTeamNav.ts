// 首页那一格目录的判断：取数、写回、盯路由。
//
// `views/home/HomeNav.vue` 只吃 props 出事件（A 档），四个弹窗也只吃 props 出事件；
// 真去问后端、真去跳地址、真去确认「退不退得掉」的都在这一层。两端（桌面侧栏
// `HomeSidebar`、手机整页 `HomeHub`）各挂一份目录，判断只有这一份。
import type { Team, TeamMember } from '@/types'

import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { toast } from 'vuetify-sonner'
import { useEventListener } from '@vueuse/core'

import { awaitingCount } from '@/composables/useAwaitingCount'
import { useJoinSpace } from '@/composables/useJoinSpace'

import { ApiError } from '@/api'
import { createAvatar } from '@/api/avatars'
import { SpacesApi } from '@/api/spaces'
import { TeamsApi } from '@/api/teams'
import { t } from '@/i18n'
import { useDialog } from '@/plugins/dialog'
import AccountService from '@/services/account'
import errorHandler from '@/services/ErrorHandler'
import { useWorkspaceStore } from '@/stores/workspace'

/** 目录里一个空间那一行只要 id 和名字；点进去是那个空间自己的目录。 */
export interface SpaceLink {
  id: number
  name: string
}

export function useTeamNav() {
  const route = useRoute()
  const router = useRouter()
  const dialog = useDialog()

  const teams = ref<Team[]>([])
  const spaces = ref<SpaceLink[]>([])

  // 名单会被重读很多次（见下面的换页面、拿回焦点）。只认最后发出的那一次，而这里
  // 自己改过名单（退出、解散、转让、改资料）也算一次更新：早发的读晚回来，会把刚退出
  // 的团队又画回去。
  let teamsRead = 0
  function setTeamsHere(next: Team[]) {
    teamsRead += 1
    teams.value = next
  }
  async function loadTeams() {
    const read = ++teamsRead
    try {
      const mine = (await TeamsApi.getMyTeams()).teams
      if (read === teamsRead) teams.value = mine
    } catch {
      // 读不到就不列：这是一份目录，不是这一页的内容。
    }
  }

  async function loadSpaces() {
    try {
      const data = await SpacesApi.list({ pageSize: 50, sort_by: 'created_at', sort_order: 'desc' })
      spaces.value = data.spaces.map((space) => ({ id: space.id, name: space.name }))
    } catch {
      // 同上。
    }
  }

  onMounted(() => {
    void loadTeams()
    void loadSpaces()
  })

  // 侧栏跨页面一直挂着，只在挂载时读一次的话，刚建的团队、刚被批准加入的团队要整页
  // 刷新才出现。没有推送告诉它名单变了，于是在人做了点什么的时候重读：换页面（建完
  // 团队就是跳进那个团队）、窗口重新拿到焦点（批准往往是在别处等来的）。
  watch(
    () => route.path,
    () => void loadTeams()
  )
  useEventListener(window, 'focus', () => void loadTeams())

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
  } = useJoinSpace(() => void loadSpaces())

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
      setTeamsHere(teams.value.map((row) => (row.id === updated.id ? { ...row, ...updated } : row)))
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
    forgetTeam(team)
  }

  // ---- 转让团队。所有者退不掉（后端要他先转让或解散），这里就是那条出路；交出去之后
  // 我是管理员，这一行的菜单跟着新角色长（这时才有「退出团队」）。
  const transferring = ref<Team | null>(null)
  const transferCandidates = ref<TeamMember[]>([])
  const transferLoading = ref(false)
  const transferBusy = ref(false)
  const transferError = ref<string | null>(null)

  async function openTransfer(team: Team | null) {
    transferring.value = team
    transferCandidates.value = []
    transferError.value = null
    if (!team) return
    transferLoading.value = true
    try {
      const { members } = await TeamsApi.getMembers(team.id)
      transferCandidates.value = members.filter((m) => m.user.id !== AccountService.user?.id)
    } catch (e) {
      transferError.value = e instanceof Error ? e.message : t('home.nav.transferTeamLoadFailed')
    } finally {
      transferLoading.value = false
    }
  }

  async function confirmTransfer(userId: number) {
    const team = transferring.value
    if (!team || transferBusy.value) return
    transferBusy.value = true
    transferError.value = null
    try {
      const { team: updated } = await TeamsApi.transferOwner(team.id, userId)
      setTeamsHere(teams.value.map((row) => (row.id === updated.id ? { ...row, ...updated, role: 'ADMIN' } : row)))
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
    forgetTeam(team)
  }

  // 这个团队不再是我的了：这一行消失；它的项目也不再是我的，rail 上那几格跟着项目清单走；
  // 正看着它的某一页的话回待办。
  function forgetTeam(team: Team) {
    setTeamsHere(teams.value.filter((row) => row.id !== team.id))
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
