// 首页那一格目录的做事面：待办数、我所在的团队、我加入的空间，以及团队那一行 ⋯ 上能
// 做的事（邀请、改资料、转让、解散、退出）。
//
// 目录的落点有两个，桌面是首页侧栏、手机是底栏「首页」那一格的整页，两处画的是同一份
// （`HomeNavView`）。取数、读路由、把展开状态记在这台浏览器上、弹四个对话框，全都在
// 这里，两个页面各自只做「接线」。
import type { Team, TeamMember } from '@/types'

import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { toast } from 'vuetify-sonner'
import { useEventListener } from '@vueuse/core'

import { awaitingCount } from '@/composables/useAwaitingCount'

import { t } from '@/i18n'
import { spaceEntryRoute } from '@/lib/spaceEntry'
import { AvatarsApi } from '@/network/api/avatars'
import { SpacesApi } from '@/network/api/spaces'
import { TeamsApi } from '@/network/api/teams'
import { BusinessError } from '@/network/types/error'
import { useDialog } from '@/plugins/dialog'
import AccountService from '@/services/account'
import errorHandler from '@/services/ErrorHandler'
import { useWorkspaceStore } from '@/stores/workspace'

/** 团队那一行 ⋯ 里点的是哪一件。「邀请」是一条链接，不走这里。 */
export type TeamAction = 'edit' | 'transfer' | 'disband' | 'leave'

/** 四个对话框里哪一个要关。 */
export type NavDialog = 'join' | 'profile' | 'disband' | 'transfer'

/** 报上来的一份小队资料草稿（和 `TeamProfileEditDialog` 的 `save` 同一份）。 */
interface TeamProfileDraft {
  name: string
  intro: string
  avatarFile?: File
}

export function useHomeNav() {
  const route = useRoute()
  const router = useRouter()
  const dialog = useDialog()
  const awaiting = awaitingCount()

  const teams = ref<Team[]>([])
  const spaces = ref<{ id: number; name: string }[]>([])

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
      const mine = (await TeamsApi.getMyTeams()).data.teams
      if (read === teamsRead) teams.value = mine
    } catch {
      // 读不到就不列：这是一份目录，不是这一页的内容。
    }
  }

  async function loadSpaces() {
    try {
      const { data } = await SpacesApi.list({ pageSize: 50, sort_by: 'created_at', sort_order: 'desc' })
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

  // 哪些团队是展开的：记在这台浏览器上，下次打开还是那样。存不进去也不要紧。
  const OPEN_KEY = 'cheesex.homeNav.openTeams'
  function readOpen(): string[] {
    try {
      const saved: unknown = JSON.parse(localStorage.getItem(OPEN_KEY) || '[]')
      return Array.isArray(saved) ? saved.filter((v): v is string => typeof v === 'string') : []
    } catch {
      return []
    }
  }
  const openHandles = ref<string[]>(readOpen())
  function persist() {
    try {
      localStorage.setItem(OPEN_KEY, JSON.stringify(openHandles.value))
    } catch {
      // 存不进去就只在这一次有效。
    }
  }
  function toggle(handle: string) {
    openHandles.value = openHandles.value.includes(handle)
      ? openHandles.value.filter((h) => h !== handle)
      : [...openHandles.value, handle]
    persist()
  }

  // 正在看某个团队的某一页时，那个团队一定是展开的：否则侧栏上找不到「你在这儿」。
  const currentHandle = computed(() =>
    typeof route.params.handle === 'string' && route.path.startsWith('/teams/') ? route.params.handle : null
  )
  watch(
    currentHandle,
    (handle) => {
      if (handle && !openHandles.value.some((h) => h.toLowerCase() === handle.toLowerCase())) {
        openHandles.value = [...openHandles.value, handle]
        persist()
      }
    },
    { immediate: true }
  )

  // ---- 四个对话框 ----
  // 开没开、忙没忙、上一句错话都归这里：`HomeNavView` 只管画，动作报回来（`submit`），
  // 成功之后再决定关不关。
  const join = reactive({ open: false, busy: false, error: null as string | null })
  const profile = reactive({ team: null as Team | null, busy: false, error: '', nameError: '' })
  const disband = reactive({ team: null as Team | null, busy: false, error: null as string | null })
  const transfer = reactive({
    team: null as Team | null,
    members: [] as TeamMember[],
    busy: false,
    error: null as string | null,
  })

  function openJoin() {
    join.error = null
    join.open = true
  }

  function openProfile(team: Team) {
    profile.error = ''
    profile.nameError = ''
    profile.team = team
  }

  function openDisband(team: Team) {
    disband.error = null
    disband.team = team
  }

  async function openTransfer(team: Team) {
    transfer.error = null
    transfer.members = []
    transfer.team = team
    try {
      const { data } = await TeamsApi.getMembers(team.id)
      // 读回来的时候人可能已经关掉、又挑了另一个团队：只认当前这个。
      if (transfer.team?.id !== team.id) return
      transfer.members = data.members.filter((m) => m.user.id !== AccountService.user?.id)
    } catch (e) {
      transfer.error = e instanceof Error ? e.message : t('home.nav.transferTeamLoadFailed')
    }
  }

  function dismiss(which: NavDialog) {
    if (which === 'join') join.open = false
    else if (which === 'profile') profile.team = null
    else if (which === 'disband') disband.team = null
    else transfer.team = null
  }

  function onTeamAction(action: TeamAction, team: Team) {
    if (action === 'edit') openProfile(team)
    else if (action === 'transfer') void openTransfer(team)
    else if (action === 'disband') openDisband(team)
    else void leaveTeam(team)
  }

  // 用邀请码加入：加成之后直接进那个空间，刚加入的空间里通常还没有他的东西。
  async function submitJoin(code: string) {
    join.busy = true
    join.error = null
    try {
      const { data } = await SpacesApi.join({ code })
      join.open = false
      void loadSpaces()
      await router.push(spaceEntryRoute(data.space))
    } catch {
      join.error = t('work.joinFailed')
    } finally {
      join.busy = false
    }
  }

  // 改小队资料：换头像 = 先把图传上去换一个 id，再把它和另外两项一起 PATCH 上去；
  // 没换图就不带 avatarId，免得把现成的头像覆盖掉。
  async function submitProfile(draft: TeamProfileDraft) {
    const team = profile.team
    if (!team) return
    profile.busy = true
    profile.error = ''
    profile.nameError = ''
    try {
      const avatarId = draft.avatarFile ? (await AvatarsApi.createAvatar(draft.avatarFile)).data.avatarId : null
      const { data } = await TeamsApi.update(team.id, {
        name: draft.name,
        intro: draft.intro,
        ...(avatarId ? { avatarId } : {}),
      })
      profile.team = null
      onTeamUpdated(data.team)
      toast.success(t('work.teamProfile.saved'))
    } catch (e) {
      // 名字全站唯一：撞名是 409「Team name already exists」，要落到名字那一格，
      // 别和「没网」「没权限」混成同一句通用报错 —— 用户能自己换一个名字解决它。
      if (e instanceof BusinessError && e.code === 409) {
        profile.nameError = t('work.teamProfile.nameTaken')
      } else if (e instanceof BusinessError && e.code === 403) {
        profile.error = t('work.teamProfile.forbidden')
      } else {
        profile.error = t('work.teamProfile.saveFailed')
        console.error(e)
      }
    } finally {
      profile.busy = false
    }
  }

  // 解散团队：撤不回，所以要把团队名打一遍才按得下去（`DisbandTeamDialog`）。后端拒绝
  // 时理由留在弹窗里；成了就和退出一样，这一行和它的项目从侧栏上下去。
  async function submitDisband() {
    const team = disband.team
    if (!team) return
    disband.busy = true
    disband.error = null
    try {
      await TeamsApi.del(team.id)
    } catch (e) {
      disband.error = e instanceof Error ? e.message : t('home.nav.disbandTeamFailed')
      disband.busy = false
      return
    }
    disband.busy = false
    disband.team = null
    toast.success(t('home.nav.disbandTeamDone', { name: team.name }))
    forgetTeam(team)
  }

  // 转让团队：交出去之后我是管理员，这一行的菜单跟着新角色长（这时才有「退出团队」）。
  async function submitTransfer(userId: number) {
    const team = transfer.team
    if (!team) return
    transfer.busy = true
    transfer.error = null
    try {
      const { data } = await TeamsApi.transferOwner(team.id, userId)
      transfer.team = null
      onTransferred(data.team)
    } catch (e) {
      transfer.error = e instanceof Error ? e.message : t('home.nav.transferTeamFailed')
    } finally {
      transfer.busy = false
    }
  }

  function onTransferred(updated: Team) {
    setTeamsHere(teams.value.map((team) => (team.id === updated.id ? { ...team, ...updated, role: 'ADMIN' } : team)))
  }

  function onTeamUpdated(updated: Team) {
    setTeamsHere(teams.value.map((team) => (team.id === updated.id ? { ...team, ...updated } : team)))
  }

  // 退出团队：退掉的是整个团队，它的项目也一起看不到了，所以先确认。退出这一下成功了
  // 就算成功，后面的刷新失败不改口。
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
    awaiting,
    teams,
    spaces,
    openHandles,
    join,
    profile,
    disband,
    transfer,
    toggle,
    openJoin,
    onTeamAction,
    dismiss,
    submitJoin,
    submitProfile,
    submitDisband,
    submitTransfer,
  }
}
