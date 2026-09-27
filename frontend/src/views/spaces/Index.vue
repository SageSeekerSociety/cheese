<template>
  <!-- 广告词只给第一次来的人看，而底栏点进来的是每天回来的人：手机上它连
         内边距要吃掉 128px，一屏去掉一大半，下面才是你来这儿要用的东西。 -->
  <div v-if="mdAndUp" class="header-corner-glow-flow">
    <PageHeader icon="mdi-view-dashboard" title="空间"></PageHeader>
    <div class="w-100 pa-8 py-16">
      <div class="text-h4 text-high-emphasis">在知是，灵感启航。</div>
      <div class="text-subtitle-1 text-medium-emphasis mt-1">让你的学术好奇心，在此与一个好课题相遇。</div>
    </div>
  </div>
  <v-container fluid>
    <v-sheet v-if="AccountService.loggedIn" border rounded="lg" class="pa-4 mb-4">
      <h2 class="text-h6 mb-3">{{ t('spaces.review.mine') }}</h2>
      <v-alert v-if="applicationsError" type="error" variant="tonal" class="mb-3">
        {{ t('spaces.review.loadFailed') }}
        <v-btn variant="text" @click="loadApplications">{{ t('spaces.review.retry') }}</v-btn>
      </v-alert>
      <p v-if="!applications.length && !applicationsError" class="text-body-2 text-medium-emphasis">
        {{ t('spaces.review.emptyMine') }}
      </p>
      <div v-for="item in applications" :key="item.id" class="py-3">
        <div class="d-flex flex-wrap align-center ga-2 mb-1">
          <v-avatar v-if="item.avatarId" size="32" :image="getAvatarUrl(item.avatarId)" />
          <h3 class="text-body-1 font-weight-medium application-copy">{{ item.name }}</h3>
          <span class="text-body-2 text-medium-emphasis">{{ t(`spaces.review.${item.reviewStatus}`) }}</span>
        </div>
        <p class="text-body-2 application-copy">{{ item.reviewReason || item.intro }}</p>
        <v-btn v-if="item.reviewStatus === 'REJECTED'" class="mt-2" variant="text" @click="openResubmit(item)">{{
          t('spaces.review.resubmit')
        }}</v-btn>
        <!-- 这一格和下面的空间卡片走**同一个**落点函数（`spaceEntryRoute`），谁也别
             自己拼地址 —— 从前这里写死 `/spaces/{id}`，那条地址 redirect 到老树，
             于是「进去」有两套意思，改一处就会漏掉另一处。 -->
        <v-btn v-if="item.reviewStatus === 'APPROVED'" class="mt-2" variant="text" :to="spaceEntryRoute(item)">{{
          t('spaces.review.enter')
        }}</v-btn>
      </div>
      <div v-if="applicationOffset || applications.length === 50" class="d-flex justify-end">
        <v-btn variant="text" :disabled="!applicationOffset" @click="changeApplicationsPage(-50)">{{
          t('spaces.review.previous')
        }}</v-btn>
        <v-btn variant="text" :disabled="applications.length < 50" @click="changeApplicationsPage(50)">{{
          t('spaces.review.next')
        }}</v-btn>
      </div>
    </v-sheet>
    <!-- 这一页通篇是**别人**的空间，没有一个字说他自己的东西从哪儿开——登录后
         的首页现在是「我的工作」（/work），空间列表降级成了它顶部的一排。这一格
         只给一个项目都没有的人看；有项目的人这一页还是原来那副样子。 -->
    <v-sheet v-if="firstRun" border rounded="lg" class="pa-4 mb-4">
      <h2 class="text-h6 font-weight-medium mb-1">从这里开始</h2>
      <p class="text-body-2 text-medium-emphasis mb-3">
        建一个项目，进去就能和芝士开工：说清楚你想做什么，它帮你查资料、写文档、拆任务。
      </p>
      <v-btn color="primary" variant="flat" prepend-icon="mdi-plus" @click="startProject">新建项目</v-btn>
      <p class="text-caption text-medium-emphasis mt-3 mb-0">或者浏览下面的空间，看看别人在做什么</p>
    </v-sheet>
    <v-row no-gutters>
      <v-col cols="12">
        <v-card class="search-card elevation-0">
          <v-card-title class="d-flex align-center justify-space-between pb-0 pt-4 px-4">
            <div class="d-flex align-center">
              <span class="text-h6">探索空间</span>
            </div>
            <v-btn
              v-if="AccountService.loggedIn"
              color="primary"
              variant="flat"
              prepend-icon="mdi-plus"
              @click="openCreateSpace"
              >{{ t('spaces.create.open') }}</v-btn
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
            <infinite-scroll
              :has-more="hasMore"
              :loading="loadingMore"
              :initial-loading="refreshing"
              :is-empty="spaces.length === 0"
              @load-more="loadMore"
            >
              <template #empty>
                <div class="empty-state-container py-6">
                  <v-empty-state title="暂无空间" icon="mdi-google-maps" class="custom-empty-state" />
                </div>
              </template>
              <v-row>
                <v-col v-for="space in spaces" :key="space.id" cols="12" sm="6" md="4">
                  <v-card flat rounded="lg" class="space-card elevation-0 border" :to="spaceEntryRoute(space)">
                    <v-card-item>
                      <!-- 首字母走 text-surface 而不是 text-white：底色是琥珀，深色主题下
                           它会提亮到 #FFA733，白字只有 1.9:1；surface 在深色下是深墨。 -->
                      <v-avatar size="60" color="primary" class="mt-2 mb-4">
                        <v-img v-if="space.avatarId" :src="getAvatarUrl(space.avatarId)">
                          <!-- seed avatars may be invalid; fall back to the initial.
                               The #error slot fills the v-img, so the char must be a
                               flex-centered fill or it sits top-left, not centered. -->
                          <template #error>
                            <span class="space-avatar-char text-h5 text-surface font-weight-medium">{{
                              (space.name || '·').trim().charAt(0)
                            }}</span>
                          </template>
                        </v-img>
                        <span v-else class="space-avatar-char text-h5 text-surface font-weight-medium">{{
                          (space.name || '·').trim().charAt(0)
                        }}</span>
                      </v-avatar>
                      <v-card-title class="text-h6 mb-2">{{ space.name }}</v-card-title>
                      <v-card-subtitle class="text-body-2 text-medium-emphasis">{{ space.intro }}</v-card-subtitle>
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
  <v-dialog v-model="createDialog" max-width="520" :persistent="creating">
    <v-card>
      <v-card-title class="d-flex align-center ga-2">
        <span>{{ resubmittingId === null ? t('spaces.create.open') : t('spaces.review.resubmit') }}</span>
        <v-spacer />
        <!-- 建版不挑模板：建出来就是课程空间。这句说清默认值，免得有人去找选择器。 -->
        <v-chip v-if="resubmittingId === null" size="small" color="primary" variant="flat">
          {{ t('spaces.create.courseTemplateTag') }}
        </v-chip>
      </v-card-title>
      <v-form @submit.prevent="createSpace">
        <v-card-text>
          <v-alert v-if="resubmittingId === null" type="info" variant="tonal" class="mb-4">
            {{ t('spaces.create.courseTemplate') }}
          </v-alert>
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
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" :disabled="creating" @click="createDialog = false">{{
            t('spaces.create.cancel')
          }}</v-btn>
          <v-btn
            type="submit"
            color="primary"
            variant="flat"
            :loading="creating"
            :disabled="!spaceName.trim() || creating"
          >
            {{ t('spaces.create.submit') }}
          </v-btn>
        </v-card-actions>
      </v-form>
    </v-card>
  </v-dialog>

  <!-- 建完版当场把邀请码给他：码是后端建版时就发好的，创建者不看着它就没处知道。 -->
  <v-dialog v-model="codeDialog" max-width="460">
    <v-card :title="t('spaces.inviteCodes.createdTitle')">
      <v-card-text>
        <p class="text-body-2 mb-3">{{ t('spaces.inviteCodes.createdBody') }}</p>
        <div class="d-flex align-center ga-2">
          <span class="invite-code-text">{{ createdInviteCode }}</span>
          <v-btn
            :icon="codeCopied ? 'mdi-check' : 'mdi-content-copy'"
            size="small"
            variant="text"
            :title="t('spaces.inviteCodes.copy')"
            @click="copyCreatedCode"
          ></v-btn>
        </div>
      </v-card-text>
      <v-card-actions>
        <v-spacer />
        <!-- 建完不停在名录页：收起这张卡就进这门课。 -->
        <v-btn color="primary" variant="flat" @click="enterCreatedSpace">
          {{ t('spaces.inviteCodes.openCourse') }}
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script lang="ts" setup>
import type { PostSpaceRequestData, SpaceApplication } from '@/network/api/spaces/types'
import type { Space } from '@/types'

import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import { useDisplay } from 'vuetify'

import { getAvatarUrl } from '@/utils/materials'
import { usePaging } from '@/utils/paging'

import { useNewProjectDialog } from '@/composables/useNewProjectDialog'

import { listProjects } from '@/api'
import AvatarUploader from '@/components/common/AvatarUploader.vue'
import InfiniteScroll from '@/components/common/InfiniteScroll.vue'
import PageHeader from '@/components/common/PageHeader.vue'
import { spaceEntryRoute } from '@/lib/courseNav'
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
const codeCopied = ref(false)
// 刚建出来的版：建完要落到这块板上，不是回到名录页干看着一个空版。
const createdSpace = ref<Space | null>(null)

function enterCreatedSpace() {
  codeDialog.value = false
  const space = createdSpace.value
  if (!space) return
  // 落点和列表卡片同一个函数：新建的板就是课，但落点不看这个 —— 都是题目板。
  void router.push(spaceEntryRoute(space))
}

async function copyCreatedCode() {
  if (!createdInviteCode.value) return
  try {
    await navigator.clipboard.writeText(createdInviteCode.value)
    codeCopied.value = true
    setTimeout(() => (codeCopied.value = false), 1600)
  } catch {
    // 剪贴板被拒（非安全上下文 / 没授权）——码还留在框里，手工选中复制即可。
  }
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
      codeCopied.value = false
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
} = usePaging(async (pageStart) => {
  const { data } = await SpacesApi.list({
    sort_by: 'createdAt',
    sort_order: 'desc',
    pageStart: pageStart,
    pageSize: 12,
  })
  return { data: data.spaces, page: data.page }
})

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
  transition: all 0.3s ease;
  overflow: hidden;
}

.border {
  border: 1px solid rgba(var(--v-theme-on-surface), 0.08);
}

.search-field {
  transition: all 0.3s ease;
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
  transition: all 0.2s ease;
  height: 100%;
  border: 1px solid transparent;
}

.space-avatar-char {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 100%;
  line-height: 1;
}

.space-card:hover {
  background-color: rgba(var(--v-theme-primary), 0.04);
  border-color: rgba(var(--v-theme-primary), 0.1);
  transform: translateY(-2px);
}

.empty-state-container {
  display: flex;
  justify-content: center;
  align-items: center;
}

.custom-empty-state:deep(.v-empty-state__icon) {
  color: var(--v-theme-primary);
  opacity: 0.9;
}
</style>
