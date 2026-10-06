<template>
  <IndexView
    v-model:create-dialog="createDialog"
    v-model:space-name="spaceName"
    v-model:space-intro="spaceIntro"
    v-model:selected-avatar="selectedAvatar"
    v-model:code-dialog="codeDialog"
    :logged-in="loggedIn"
    :applications="applications"
    :applications-error="applicationsError"
    :application-offset="applicationOffset"
    :first-run="firstRun"
    :spaces="spaces"
    :spaces-failed="spacesFailed"
    :load-error="loadError"
    :forbidden="forbidden"
    :has-more="hasMore"
    :loading-more="loadingMore"
    :refreshing="refreshing"
    :total="total"
    :resubmitting-id="resubmittingId"
    :creating="creating"
    :existing-avatar-id="existingAvatarId"
    :create-error="createError"
    :created-invite-code="createdInviteCode"
    @load-applications="loadApplications"
    @change-applications-page="changeApplicationsPage"
    @open-resubmit="openResubmit"
    @open-create-space="openCreateSpace"
    @start-project="startProject"
    @refresh="refresh"
    @load-more="loadMore"
    @create-space="createSpace"
    @copy-created-code="copyCreatedCode"
    @enter-created-space="enterCreatedSpace"
  />
</template>

<script lang="ts" setup>
import type { PostSpaceRequestData, SpaceApplication } from '@/network/api/spaces/types'
import type { Space } from '@/types'

import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'

import { usePaging } from '@/utils/paging'

import { useNewProjectDialog } from '@/composables/useNewProjectDialog'

import IndexView from './IndexView.vue'

import { listProjects } from '@/api'
import { copyText } from '@/commands/copy'
import { isForbidden, loadFailureReason } from '@/lib/loadFailure'
import { spaceEntryRoute } from '@/lib/spaceEntry'
import { AvatarsApi } from '@/network/api/avatars'
import { SpacesApi } from '@/network/api/spaces'
import AccountService from '@/services/account'

const { t } = useI18n()
const router = useRouter()
const loggedIn = AccountService.loggedIn
const applications = ref<SpaceApplication[]>([])
const applicationsError = ref(false)
const applicationOffset = ref(0)
const resubmittingId = ref<number | null>(null)

async function loadApplications() {
  applicationsError.value = false
  try {
    applications.value = (await SpacesApi.applications(applicationOffset.value)).data.items
  } catch {
    applicationsError.value = true
  }
}

function changeApplicationsPage(delta: number) {
  applicationOffset.value += delta
  void loadApplications()
}

function openResubmit(item: SpaceApplication) {
  resubmittingId.value = item.id
  spaceName.value = item.name
  spaceIntro.value = item.intro
  selectedAvatar.value = undefined
  existingAvatarId.value = item.avatarId ?? undefined
  createError.value = ''
  createDialog.value = true
}
const createDialog = ref(false)
const creating = ref(false)
const spaceName = ref('')
const spaceIntro = ref('')
const selectedAvatar = ref<File>()
const existingAvatarId = ref<number>()
const createError = ref('')
// 建版的响应里就带着刚发好的邀请码，从前这里是 `await SpacesApi.create(payload)`
// 把整个响应丢掉，于是创建者根本不知道自己的码是什么。留住它，建完当场给他看。
const createdInviteCode = ref<string | null>(null)
const codeDialog = ref(false)
// 刚建出来的版：建完要落到这块板上，不是回到名录页干看着一个空版。
const createdSpace = ref<Space | null>(null)

function enterCreatedSpace() {
  codeDialog.value = false
  const space = createdSpace.value
  if (!space) return
  // 落点和列表卡片同一个函数：新建的板就是课，但落点不看这个 —— 都是题目板。
  void router.push(spaceEntryRoute(space))
}

// 复制成的说法交给共享的复制助手（一条 toast），按钮不再自己换成「已复制」。
async function copyCreatedCode() {
  if (!createdInviteCode.value) return
  await copyText(createdInviteCode.value, t('navigation.copy.done'))
}

function openCreateSpace() {
  resubmittingId.value = null
  spaceName.value = ''
  spaceIntro.value = ''
  selectedAvatar.value = undefined
  existingAvatarId.value = undefined
  createdSpace.value = null
  createError.value = ''
  createDialog.value = true
}

async function createSpace() {
  if (creating.value || !spaceName.value.trim()) return
  creating.value = true
  createError.value = ''
  try {
    const payload: PostSpaceRequestData = { name: spaceName.value.trim(), intro: spaceIntro.value.trim() }
    if (selectedAvatar.value) {
      const { data } = await AvatarsApi.createAvatar(selectedAvatar.value)
      payload.avatarId = data.avatarId
    }
    if (resubmittingId.value !== null) {
      await SpacesApi.resubmit(resubmittingId.value, payload)
    } else {
      const { data } = await SpacesApi.create(payload)
      createdInviteCode.value = data.inviteCode?.code ?? null
      createdSpace.value = data.space ?? null
      // 有码先把码给他（建版时就发好了），收起那张卡再进这门课；
      // 没码就直接进。
      if (createdInviteCode.value) codeDialog.value = true
      else enterCreatedSpace()
    }
    createDialog.value = false
    applicationOffset.value = 0
    await loadApplications()
  } catch {
    createError.value = t('spaces.create.failed')
  } finally {
    creating.value = false
  }
}
const { show: showNewProjectDialog } = useNewProjectDialog()

const selectedSort = ref('newest')
const sortOptions = [
  { text: t('spaces.index.sortOptions.newest'), value: 'newest' },
  { text: t('spaces.index.sortOptions.hot'), value: 'hot' },
]

const {
  data: spaces,
  error,
  hasMore,
  loadMore,
  refresh,
  refreshing,
  loadingMore,
  total,
} = usePaging(async (pageStart) => {
  const { data } = await SpacesApi.list({
    sort_by: 'createdAt',
    sort_order: 'desc',
    pageStart: pageStart,
    pageSize: 12,
  })
  return { data: data.spaces, page: data.page }
})

// 读失败时 `spaces` 是空的，光看 `is-empty` 分不出「读失败」和「一条都没有」。
// 只有在**没有东西可显示**时才替换列表：翻页失败时前面那些卡片还在，替换掉它们
// 等于把读到的东西也一起扔了。
const spacesFailed = computed(() => error.value !== null && spaces.value.length === 0)
const loadError = computed(() => loadFailureReason(error.value))
const forbidden = computed(() => isForbidden(error.value))

const sortSpaces = (value: string) => {
  console.log(value)
}

// 「他一个项目都没有」——`listProjects()` 不带 team_id 时，后端返回的就是调用者
// 自己的项目（backend/app/api/routes/projects.py:182 把这个语义写死在那儿了），
// 所以一问即知：不新增字段、不加迁移，跟 #946 验收里「老用户可跳过引导」用的是
// 同一条判据。
//
// 不拿 `useWorkspaceStore().projects` 或 sessionStorage 里那份缓存当依据：前者
// 只在 openProject 时才填，冷启动到这一页必然为空；后者分不出「真的没有」和
// 「缓存没命中」。宁可多问一次接口。
//
// 拿不到清单就不显示——宁可少给一次提示，也不要在项目早就存在时对他说「从这
// 里开始」。
const firstRun = ref(false)
async function detectFirstRun() {
  try {
    firstRun.value = (await listProjects()).data.length === 0
  } catch {
    firstRun.value = false
  }
}

// 不传 team：对话框自己挑我的小队，没建过队的人落到个人小队（见
// `defaultTeamFor`），和桌面 rail 那个 ＋ 的行为一致。
function startProject() {
  showNewProjectDialog()
}

onMounted(async () => {
  if (AccountService.loggedIn) void loadApplications()
  void detectFirstRun()
  await refresh()
})
</script>
