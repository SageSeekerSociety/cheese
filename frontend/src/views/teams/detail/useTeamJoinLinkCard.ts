// 小队链接卡片的取数：团队成员页（Members.vue）持有它，视图（TeamJoinLinkCard.vue）
// 只认 props/emits。团队地址（handle）改写、小队链接重置、加入审批与可见性都在这儿打接口。
import type { Ref } from 'vue'
import type { Team, TeamVisibility } from '@/types'

import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { t } from '@/i18n'
import { TeamsApi } from '@/network/api/teams'
import { BusinessError } from '@/network/types/error'

export function useTeamJoinLinkCard(
  team: Ref<Team | undefined>,
  onUpdated: (team: Team) => void,
  enabled: () => boolean
) {
  const link = ref<TeamsApi.TeamJoinLink | null>(null)
  const busy = ref(false)
  const error = ref('')
  const copied = ref(false)
  const url = computed(() => (link.value ? `${window.location.origin}/team-invites/${link.value.token}` : ''))

  const route = useRoute()
  const router = useRouter()
  const addressPrefix = `${window.location.host}/teams/`
  const handle = ref(team.value?.handle ?? '')
  const handleError = ref('')
  watch(
    () => team.value?.handle,
    (current) => (handle.value = current ?? '')
  )

  // A new handle is a new address: the page moves to it, the old one stops working.
  async function saveHandle() {
    const current = team.value
    if (!current) return
    handleError.value = ''
    busy.value = true
    try {
      const {
        data: { team: updated },
      } = await TeamsApi.update(current.id, { handle: handle.value.trim() })
      onUpdated(updated)
      await router.replace({
        name: route.name ?? 'TeamsDetailDefault',
        params: { handle: updated.handle },
        query: route.query,
      })
    } catch (e) {
      handleError.value =
        e instanceof BusinessError && e.code === 409
          ? t('work.teamLink.handleTaken')
          : e instanceof BusinessError && e.code === 400
            ? t('work.teamLink.handleInvalid')
            : t('work.teamLink.failed')
    } finally {
      busy.value = false
    }
  }

  async function run(action: () => Promise<void>) {
    busy.value = true
    error.value = ''
    try {
      await action()
    } catch {
      error.value = t('work.teamLink.failed')
    } finally {
      busy.value = false
    }
  }

  // 卡片按 teamData / canBringPeopleIn 决定显不显示；这门也照它开合，免得给看不了
  // 这张卡片的人白发一次请求。
  watch(
    [() => team.value?.id, enabled],
    ([teamId, on]) => {
      link.value = null
      copied.value = false
      if (teamId === undefined || !on) return
      void run(async () => {
        const { data } = await TeamsApi.getJoinLink(teamId)
        if (team.value?.id === teamId) link.value = data
      })
    },
    { immediate: true }
  )

  function reset() {
    const current = team.value
    if (!current) return
    copied.value = false
    void run(async () => {
      link.value = (await TeamsApi.resetJoinLink(current.id)).data
    })
  }

  function setApproval(approval: boolean | null) {
    const current = team.value
    if (!current) return
    void run(async () => {
      link.value = (await TeamsApi.updateJoinLink(current.id, { approval: !!approval })).data
    })
  }

  function setVisibility(visibility: TeamVisibility) {
    const current = team.value
    if (!current) return
    void run(async () => {
      const {
        data: { team: updated },
      } = await TeamsApi.update(current.id, { visibility })
      onUpdated(updated)
    })
  }

  async function copy() {
    error.value = ''
    try {
      await navigator.clipboard.writeText(url.value)
      copied.value = true
    } catch {
      error.value = t('work.teamLink.copyFailed')
    }
  }

  return {
    link,
    busy,
    error,
    copied,
    url,
    addressPrefix,
    handle,
    handleError,
    saveHandle,
    reset,
    setApproval,
    setVisibility,
    copy,
  }
}
