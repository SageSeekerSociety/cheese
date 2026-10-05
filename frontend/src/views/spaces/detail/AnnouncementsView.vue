<script setup lang="ts">
// 空间公告这一屏的画面：公告卡片那一列、已到期那一折、发布/编辑那个对话框。取数、发
// 布/改/删/置顶的调用都归容器 `Announcements.vue`（场景规则见 docs/manual/dev/scenes.md）。
import type { SpaceAnnouncement } from '@/types'

import { computed, defineAsyncComponent, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import { dayOfExpiry, expiryFromDay } from '../model'

import AnnouncementCard from './AnnouncementCard.vue'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseEmptyState from '@/components/base/BaseEmptyState.vue'
import AdaptiveDialog from '@/components/common/AdaptiveDialog.vue'
import PageHeader from '@/components/common/PageHeader.vue'

const TipTapEditor = defineAsyncComponent(() => import('@/components/common/Editor/TipTapEditor.vue'))

export interface AnnouncementDraft {
  id: number | null
  title: string
  content: string
  pinned: boolean
  expiresAt: number | null
}

const props = defineProps<{
  current: SpaceAnnouncement[]
  expired: SpaceAnnouncement[]
  loaded: boolean
  isManager: boolean
  submitting: boolean
  notifyCount: number
}>()

const emit = defineEmits<{
  submit: [draft: AnnouncementDraft]
  pin: [announcement: SpaceAnnouncement]
  remove: [announcement: SpaceAnnouncement]
}>()

const editing = defineModel<boolean>('open', { required: true })

const { t } = useI18n()

const showExpired = ref(false)
/** 正在改的那一条；发布新公告时为空。 */
const editingId = ref<number | null>(null)
const draftTitle = ref('')
const draftContent = ref('')
const draftPinned = ref(false)
/** `<input type="date">` 的值，`YYYY-MM-DD`；空 = 不到期。 */
const draftExpiry = ref('')

function openCreate() {
  editingId.value = null
  draftTitle.value = ''
  draftContent.value = ''
  draftPinned.value = false
  draftExpiry.value = ''
  editing.value = true
}

function openEdit(a: SpaceAnnouncement) {
  editingId.value = a.id
  draftTitle.value = a.title
  draftContent.value = a.content
  draftExpiry.value = a.expiresAt === null ? '' : dayOfExpiry(a.expiresAt)
  editing.value = true
}

/** 今天之前的日子不能选：发出去就已经到期的公告没有意义。 */
const earliestExpiry = dayOfExpiry(Date.now() + 1)

const notifyHint = computed(() =>
  props.notifyCount ? t('spaces.announcements.form.notifyHint', { count: props.notifyCount }) : ''
)

function submit() {
  const title = draftTitle.value.trim()
  if (!title) return
  const expiresAt = draftExpiry.value ? expiryFromDay(draftExpiry.value) : null
  emit('submit', {
    id: editingId.value,
    title,
    content: draftContent.value,
    pinned: draftPinned.value,
    expiresAt,
  })
}
</script>

<template>
  <PageHeader :title="t('spaces.detail.announcements')" show-on-mobile>
    <!-- 插槽本身不能带 v-if：PageHeader 只在挂上那一刻看有没有操作区插槽，空间读回来、
         知道你是管理员时它已经不再看了。条件放在按钮上。 -->
    <template #actions>
      <BaseButton v-if="isManager" kind="primary" @click="openCreate">
        {{ t('spaces.announcements.publish') }}
      </BaseButton>
    </template>
  </PageHeader>
  <div class="ann">
    <AnnouncementCard
      v-for="a in current"
      :key="a.id"
      :announcement="a"
      :manager="isManager"
      @pin="emit('pin', a)"
      @edit="openEdit(a)"
      @remove="emit('remove', a)"
    />

    <button
      v-if="expired.length"
      type="button"
      class="ann__fold t-body"
      :aria-expanded="showExpired"
      @click="showExpired = !showExpired"
    >
      {{ t('spaces.announcements.expiredFold', { count: expired.length }) }}
      <v-icon :icon="showExpired ? 'mdi-chevron-up' : 'mdi-chevron-down'" size="18" class="ann__fold-icon" />
    </button>

    <template v-if="showExpired">
      <AnnouncementCard
        v-for="a in expired"
        :key="a.id"
        :announcement="a"
        expired
        :manager="isManager"
        @edit="openEdit(a)"
        @remove="emit('remove', a)"
      />
    </template>

    <BaseEmptyState
      v-if="loaded && !current.length && !expired.length"
      size="inline"
      :title="t('spaces.announcements.empty')"
    />

    <AdaptiveDialog
      v-model="editing"
      :title="t(editingId === null ? 'spaces.announcements.publish' : 'spaces.announcements.editTitle')"
      :primary-label="t(editingId === null ? 'spaces.announcements.form.publish' : 'spaces.announcements.form.save')"
      :primary-loading="submitting"
      :primary-disabled="!draftTitle.trim()"
      @primary="submit"
    >
      <v-text-field
        v-model="draftTitle"
        autocomplete="off"
        :label="t('spaces.announcements.form.title')"
        variant="outlined"
        density="comfortable"
        maxlength="255"
        :counter="255"
        persistent-counter
      />
      <TipTapEditor v-model="draftContent" output="html" :aria-label="t('spaces.announcements.form.content')" />
      <div class="ann__opts">
        <!-- Pinning is only chosen while posting; a published announcement uses the card's own action. -->
        <v-checkbox
          v-if="editingId === null"
          v-model="draftPinned"
          :label="t('spaces.announcements.form.pinned')"
          density="comfortable"
          hide-details
          color="primary"
        />
        <v-text-field
          v-model="draftExpiry"
          type="date"
          :label="t('spaces.announcements.form.expiry')"
          variant="outlined"
          density="comfortable"
          hide-details
          clearable
          :min="earliestExpiry"
          class="ann__expiry"
        />
      </div>
      <p v-if="editingId === null && notifyHint" class="ann__hint">{{ notifyHint }}</p>
    </AdaptiveDialog>
  </div>
</template>

<style scoped>
.ann {
  display: flex;
  flex-direction: column;
  gap: 10px;
  /* 宽屏下封顶居中了，不再左贴：以前 max-width 之外没有 auto，右边会空出一条
     随窗口变宽的边。 */
  max-width: 880px;
  margin-inline: auto;
  padding: 16px;
}

.ann__fold {
  display: flex;
  align-items: center;
  width: 100%;
  min-height: 44px;
  padding: 0 16px;
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  background: var(--surface);
  color: var(--muted);
  font-family: inherit;
  text-align: left;
  cursor: pointer;
}

.ann__fold:hover {
  background: var(--fill);
}

.ann__fold-icon {
  margin-left: auto;
}

.ann__opts {
  display: flex;
  flex-wrap: wrap;
  gap: 16px 24px;
  align-items: center;
  margin-top: 16px;
}

.ann__expiry {
  flex: 1;
  min-width: 200px;
}

.ann__hint {
  margin: 16px 0 0;
  padding: 10px 12px;
  border-radius: var(--radius-md);
  background: var(--fill);
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
}
</style>
