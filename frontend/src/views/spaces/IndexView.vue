<!--
  The rendering half of Index.vue, split out by `pnpm run scene:split`: props in,
  events out. The page keeps the route, the requests and the stores.
-->

<script setup lang="ts">
import type { Space } from '@/types'
import type { SpaceApplication } from '@/types/apiSpaces'

import { useI18n } from 'vue-i18n'

import { getAvatarUrl } from '@/utils/materials'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import BaseLoadError from '@/components/base/BaseLoadError.vue'
import { DIALOG_WIDTH } from '@/components/base/dialogSize'
import AdaptiveDialog from '@/components/common/AdaptiveDialog.vue'
import AvatarUploader from '@/components/common/AvatarUploader.vue'
import InfiniteScroll from '@/components/common/InfiniteScroll.vue'
import PageHeader from '@/components/common/PageHeader.vue'
import UserAvatar from '@/components/common/UserAvatar.vue'
import { isForbidden, loadFailureReason } from '@/lib/loadFailure'
import { spaceEntryRoute } from '@/lib/spaceEntry'

defineProps<{
  mdAndUp: boolean
  /** 登录状态由页面给的，视图自己不认 `AccountService` —— 认了它就出不了 A 级。 */
  loggedIn: boolean
  applicationsError: boolean
  applications: SpaceApplication[]
  applicationOffset: number
  firstRun: boolean
  spacesFailed: boolean
  error: Error | null
  refresh: () => Promise<void>
  hasMore: boolean
  loadingMore: boolean
  refreshing: boolean
  spaces: Space[]
  total: number
  loadMore: () => Promise<void>
  resubmittingId: number | null
  creating: boolean
  existingAvatarId: number | undefined
  createError: string
  createdInviteCode: string | null
}>()
const emit = defineEmits<{
  'load-applications': []
  'open-resubmit': [item: SpaceApplication]
  'change-applications-page': [delta: number]
  'start-project': []
  'open-create-space': []
  'create-space': []
  'copy-created-code': []
  'enter-created-space': []
}>()
const createDialog = defineModel<boolean>('createDialog', { required: true })
const spaceName = defineModel<string>('spaceName', { required: true })
const selectedAvatar = defineModel<File | undefined>('selectedAvatar', { required: true })
const spaceIntro = defineModel<string>('spaceIntro', { required: true })
const codeDialog = defineModel<boolean>('codeDialog', { required: true })
const { t } = useI18n()
</script>

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
    <v-sheet v-if="loggedIn" border rounded="lg" class="pa-4 mb-4">
      <h2 class="text-h6 mb-3">{{ t('spaces.review.mine') }}</h2>
      <v-alert v-if="applicationsError" type="error" variant="tonal" class="mb-3">
        {{ t('spaces.review.loadFailed') }}
        <BaseButton kind="secondary" @click="emit('load-applications')">{{ t('spaces.review.retry') }}</BaseButton>
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
        <BaseButton
          v-if="item.reviewStatus === 'REJECTED'"
          kind="ghost"
          class="mt-2"
          @click="emit('open-resubmit', item)"
          >{{ t('spaces.review.resubmit') }}</BaseButton
        >
        <!-- 这一格和下面的空间卡片走**同一个**落点函数（`spaceEntryRoute`），谁也别
             自己拼地址 —— 从前这里写死 `/spaces/{id}`，那条地址 redirect 到老树，
             于是「进去」有两套意思，改一处就会漏掉另一处。 -->
        <BaseButton v-if="item.reviewStatus === 'APPROVED'" kind="ghost" class="mt-2" :to="spaceEntryRoute(item)">{{
          t('spaces.review.enter')
        }}</BaseButton>
      </div>
      <div v-if="applicationOffset || applications.length === 50" class="d-flex justify-end">
        <BaseButton kind="ghost" :disabled="!applicationOffset" @click="emit('change-applications-page', -50)">{{
          t('spaces.review.previous')
        }}</BaseButton>
        <BaseButton kind="ghost" :disabled="applications.length < 50" @click="emit('change-applications-page', 50)">{{
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
      <BaseButton kind="primary" prepend-icon="mdi-plus" @click="emit('start-project')">{{
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
            <BaseButton v-if="loggedIn" kind="primary" prepend-icon="mdi-plus" @click="emit('open-create-space')">{{
              t('spaces.create.open')
            }}</BaseButton>
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
    @primary="emit('create-space')"
  >
    <v-form @submit.prevent="emit('create-space')">
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
      <!-- 和 NewChannelDialog 同例：这个弹窗就是为写一个空间名而开，打开它本身就是用户的
           动作，焦点直接落在那一格上。 -->
      <!-- eslint-disable vuejs-accessibility/no-autofocus -- 见上一句 -->
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
      <!-- eslint-enable vuejs-accessibility/no-autofocus -->
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
  <v-dialog v-model="codeDialog" :max-width="DIALOG_WIDTH.sm">
    <v-card :title="t('spaces.inviteCodes.createdTitle')">
      <v-card-text>
        <p class="text-body-2 mb-3">{{ t('spaces.inviteCodes.createdBody') }}</p>
        <div class="d-flex align-center ga-2">
          <span class="invite-code-text">{{ createdInviteCode }}</span>
          <BaseButton
            kind="ghost"
            icon="mdi-content-copy"
            size="sm"
            :title="t('spaces.inviteCodes.copy')"
            @click="emit('copy-created-code')"
          />
        </div>
      </v-card-text>
      <v-card-actions>
        <v-spacer />
        <!-- Don't stop back on the list after creating: dismissing this card enters the space. -->
        <BaseButton kind="primary" @click="emit('enter-created-space')">
          {{ t('spaces.inviteCodes.openSpace') }}
        </BaseButton>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

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
