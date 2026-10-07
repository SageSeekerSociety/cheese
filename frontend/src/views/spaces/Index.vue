<template>
  <!-- 广告词只给第一次来的人看，而底栏点进来的是每天回来的人：手机上它连
         内边距要吃掉 128px，一屏去掉一大半，下面才是你来这儿要用的东西。 -->
  <div v-if="mdAndUp" class="header-corner-glow-flow">
    <PageHeader icon="mdi-view-dashboard" :title="t('spaces.index.title')"></PageHeader>
    <div class="w-100 pa-8 py-16">
      <div class="text-h4 text-high-emphasis">{{ t('spaces.index.heroTitle') }}</div>
      <div class="text-subtitle-1 text-medium-emphasis mt-1">{{ t('spaces.index.heroSubtitle') }}</div>
    </div>
  </div>
  <v-container fluid>
    <v-sheet v-if="AccountService.loggedIn" border rounded="lg" class="pa-4 mb-4">
      <h2 class="text-h6 mb-3">{{ t('spaces.review.mine') }}</h2>
      <v-alert v-if="applicationsError" type="error" variant="tonal" class="mb-3">
        {{ t('spaces.review.loadFailed') }}
        <BaseButton kind="secondary" @click="loadApplications">{{ t('spaces.review.retry') }}</BaseButton>
      </v-alert>
      <p v-if="!applications.length && !applicationsError" class="text-body-2 text-medium-emphasis">
        {{ t('spaces.review.emptyMine') }}
      </p>
      <div v-for="item in applications" :key="item.id" class="py-3">
        <div class="d-flex flex-wrap align-center ga-2 mb-1">
          <UserAvatar kind="org" :avatar="getAvatarUrl(item.avatarId)" :name="item.name" size="32" />
          <h3 class="text-body-1 font-weight-medium application-copy" data-user-content>{{ item.name }}</h3>
          <span class="text-body-2 text-medium-emphasis">{{ t(`spaces.review.${item.reviewStatus}`) }}</span>
        </div>
        <p class="text-body-2 application-copy" data-user-content>{{ item.reviewReason || item.intro }}</p>
        <BaseButton v-if="item.reviewStatus === 'REJECTED'" kind="ghost" class="mt-2" @click="openResubmit(item)">{{
          t('spaces.review.resubmit')
        }}</BaseButton>
        <!-- 这一格和下面的空间卡片走**同一个**落点函数（`spaceEntryRoute`），谁也别
             自己拼地址 —— 从前这里写死 `/spaces/{id}`，那条地址 redirect 到老树，
             于是「进去」有两套意思，改一处就会漏掉另一处。 -->
        <BaseButton v-if="item.reviewStatus === 'APPROVED'" kind="ghost" class="mt-2" :to="spaceEntryRoute(item)">{{
          t('spaces.review.enter')
        }}</BaseButton>
      </div>
      <div v-if="applicationOffset || applications.length === 50" class="d-flex justify-end">
        <BaseButton kind="ghost" :disabled="!applicationOffset" @click="changeApplicationsPage(-50)">{{
          t('spaces.review.previous')
        }}</BaseButton>
        <BaseButton kind="ghost" :disabled="applications.length < 50" @click="changeApplicationsPage(50)">{{
          t('spaces.review.next')
        }}</BaseButton>
      </div>
    </v-sheet>
    <!-- 这一页通篇是**别人**的空间，没有一个字说他自己的东西从哪儿开。这一格
         只给一个项目都没有的人看；有项目的人这一页还是原来那副样子。 -->
    <v-sheet v-if="firstRun" border rounded="lg" class="pa-4 mb-4">
      <h2 class="text-h6 font-weight-medium mb-1">{{ t('spaces.index.firstRun.title') }}</h2>
      <p class="text-body-2 text-medium-emphasis mb-3">
        {{ t('spaces.index.firstRun.body') }}
      </p>
      <BaseButton kind="primary" prepend-icon="mdi-plus" @click="startProject">{{
        t('spaces.index.firstRun.newProject')
      }}</BaseButton>
      <p class="text-caption text-medium-emphasis mt-3 mb-0">{{ t('spaces.index.firstRun.browse') }}</p>
    </v-sheet>
    <v-row no-gutters>
      <v-col cols="12">
        <v-card class="search-card elevation-0">
          <v-card-title class="d-flex align-center justify-space-between pb-0 pt-4 px-4">
            <div class="d-flex align-center">
              <span class="text-h6">{{ t('spaces.index.explore') }}</span>
            </div>
            <BaseButton
              v-if="AccountService.loggedIn"
              kind="primary"
              prepend-icon="mdi-plus"
              @click="openCreateSpace"
              >{{ t('spaces.create.open') }}</BaseButton
            >
            <!-- <v-btn-toggle v-model="selectedSort" class="sort-toggle" rounded="lg" color="primary" density="comfortable">
              <v-btn
                v-for="(item, index) in sortOptions"
                :key="index"
                :value="item.value"
                @click="sortSpaces(item.value)"
              >
                <v-icon
                  :icon="item.value === 'newest' ? 'mdi-clock-outline' : 'mdi-fire'"
                  size="small"
                  class="mr-1"
                ></v-icon>
                {{ item.text }}
              </v-btn>
            </v-btn-toggle> -->
          </v-card-title>
          <v-card-text class="px-4 py-4">
            <!-- A failed read leaves `spaces` empty, so the scroll area would say "no spaces yet" — the same shape as a space list that really is empty. Replace the area instead of falling into it. -->
            <BaseLoadError
              v-if="spacesFailed"
              :title="t('spaces.index.loadFailed')"
              :error="loadFailureReason(error)"
              :forbidden="isForbidden(error)"
              @retry="refresh"
            />
            <infinite-scroll
              v-else
              :has-more="hasMore"
              :loading="loadingMore"
              :initial-loading="refreshing"
              :is-empty="spaces.length === 0"
              :shown="spaces.length"
              :total="total"
              @load-more="loadMore"
            >
              <template #empty>
                <BaseEmptyState icon="mdi-google-maps" :title="t('spaces.index.noSpaces')" />
              </template>
              <v-row>
                <!-- Three cards across a wide screen stretches each one far too wide:
                     four per row from `lg`, six from `xl`, so a card stays at a
                     readable size instead of growing with the window. -->
                <v-col v-for="space in spaces" :key="space.id" cols="12" sm="6" md="4" lg="3" xl="2">
                  <v-card flat rounded="lg" class="space-card elevation-0 border" :to="spaceEntryRoute(space)">
                    <v-card-item>
                      <UserAvatar
                        kind="org"
                        :avatar="getAvatarUrl(space.avatarId)"
                        :name="space.name"
                        size="60"
                        class="mt-2 mb-4"
                      />
                      <v-card-title class="text-h6 mb-2" data-user-content>{{ space.name }}</v-card-title>
                      <v-card-subtitle class="text-body-2 text-medium-emphasis" data-user-content>{{
                        space.intro
                      }}</v-card-subtitle>
                    </v-card-item>
                  </v-card>
                </v-col>
              </v-row>
            </infinite-scroll>
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>
  </v-container>
  <AdaptiveDialog
    v-model="createDialog"
    :title="resubmittingId === null ? t('spaces.create.open') : t('spaces.review.resubmit')"
    :primary-label="t('spaces.create.submit')"
    :primary-loading="creating"
    :primary-disabled="!spaceName.trim() || creating"
    :close-disabled="creating"
    @primary="createSpace"
  >
    <v-form @submit.prevent="createSpace">
      <p class="text-body-2 mb-2">{{ t('spaces.create.ownership') }}</p>
      <p class="text-body-2 text-medium-emphasis mb-4">{{ t('spaces.create.visibility') }}</p>
      <p class="text-body-2 mb-2">{{ t('spaces.create.avatar') }}</p>
      <AvatarUploader
        v-if="createDialog"
        v-model="selectedAvatar"
        :src="existingAvatarId ? getAvatarUrl(existingAvatarId) : undefined"
        :disabled="creating"
        class="mb-4 board-avatar-picker"
      />
      <v-text-field
        v-model="spaceName"
        autocomplete="off"
        maxlength="255"
        :counter="255"
        persistent-counter
        :label="t('spaces.create.name')"
        :placeholder="t('spaces.create.placeholder')"
        :disabled="creating"
        autofocus
        variant="outlined"
      />
      <v-textarea
        v-model="spaceIntro"
        autocomplete="off"
        :label="t('spaces.create.intro')"
        :disabled="creating"
        rows="3"
        variant="outlined"
      />
      <v-alert v-if="createError" type="error" variant="tonal" role="alert">{{ createError }}</v-alert>
    </v-form>
  </AdaptiveDialog>

  <!-- Hand the invite code over the moment the board is created: the backend issues it at
       creation, so the creator has nowhere else to see it. -->
  <!-- 只有「打开空间」这一颗进新板；取消、Esc、点遮罩只是关上这张卡（迁移前也只有那
       一颗按钮会进）。 -->
  <AdaptiveDialog
    v-model="codeDialog"
    :title="t('spaces.inviteCodes.createdTitle')"
    size="sm"
    :primary-label="t('spaces.inviteCodes.openSpace')"
    @primary="enterCreatedSpace"
  >
    <p class="text-body-2 mb-3">{{ t('spaces.inviteCodes.createdBody') }}</p>
    <div class="d-flex align-center ga-2">
      <span class="invite-code-text">{{ createdInviteCode }}</span>
      <BaseButton
        kind="ghost"
        icon="mdi-content-copy"
        size="sm"
        :title="t('spaces.inviteCodes.copy')"
        @click="copyCreatedCode"
      />
    </div>
  </AdaptiveDialog>
</template>

<script lang="ts" setup>
import type { PostSpaceRequestData, SpaceApplication } from '@/network/api/spaces/types'
import type { Space } from '@/types'

import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import { useDisplay } from 'vuetify'

import { getAvatarUrl } from '@/utils/materials'
import { usePaging } from '@/utils/paging'

import { useNewProjectDialog } from '@/composables/useNewProjectDialog'

import { listProjects } from '@/api'
import { copyText } from '@/commands/copy'
import BaseButton from '@/components/base/BaseButton.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'
import AdaptiveDialog from '@/components/common/AdaptiveDialog.vue'
import AvatarUploader from '@/components/common/AvatarUploader.vue'
import InfiniteScroll from '@/components/common/InfiniteScroll.vue'
import PageHeader from '@/components/common/PageHeader.vue'
import UserAvatar from '@/components/common/UserAvatar.vue'
import { isForbidden, loadFailureReason } from '@/lib/loadFailure'
import { spaceEntryRoute } from '@/lib/spaceEntry'
import { AvatarsApi } from '@/network/api/avatars'
import { SpacesApi } from '@/network/api/spaces'
import AccountService from '@/services/account'

const { t } = useI18n()
const { mdAndUp } = useDisplay()
const router = useRouter()
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

<style scoped>
.board-avatar-picker {
  width: 120px;
}

.invite-code-text {
  font-family: var(--font-mono);
  font-size: 1.05rem;
  font-weight: 600;
  letter-spacing: 0.08em;
}

.application-copy {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.search-card {
  overflow: hidden;
}

.border {
  border: 1px solid rgba(var(--v-theme-on-surface), 0.08);
}

.search-field {
  transition: opacity 0.3s ease;
}

.search-field:deep(.v-field__outline) {
  opacity: 0.7;
}

.search-field:hover:deep(.v-field__outline) {
  opacity: 1;
}

.sort-toggle {
  border: 1px solid rgba(var(--v-theme-primary), 0.12);
  background-color: rgba(var(--v-theme-primary), 0.04);
}

.sort-toggle:deep(.v-btn) {
  text-transform: none;
  letter-spacing: 0;
}

.space-card {
  transition:
    background-color 0.2s ease,
    border-color 0.2s ease,
    transform 0.2s ease;
  height: 100%;
  border: 1px solid transparent;
}

.space-card:hover {
  background-color: rgba(var(--v-theme-primary), 0.04);
  border-color: rgba(var(--v-theme-primary), 0.1);
  transform: translateY(-2px);
}
</style>
