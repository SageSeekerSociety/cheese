<script setup lang="ts">
// 公告页上的一条：标题、正文，底下一行发布人 · 发布日期（· 已编辑）和到期日，管理员
// 另有置顶、编辑、删除三个动作。已到期的那几条不给置顶 —— 它们已经不在题目列表上了。
import type { SpaceAnnouncement } from '@/types'

import { computed, defineAsyncComponent } from 'vue'
import { useI18n } from 'vue-i18n'

import { announcementDay, expiryDay } from '../model'

import { useAnnouncementDay } from './useAnnouncementDay'

import BaseButton from '@/components/base/BaseButton.vue'

const TipTapViewer = defineAsyncComponent(() => import('@/components/common/Editor/TipTapViewer.vue'))

const props = defineProps<{ announcement: SpaceAnnouncement; expired?: boolean; manager: boolean }>()
defineEmits<{ pin: []; edit: []; remove: [] }>()

const { t } = useI18n()
const dayText = useAnnouncementDay()

/** 「已编辑」只说标题、正文或到期日改过；置顶不算，服务端也不为它挪 `updatedAt`。 */
const byline = computed(() => {
  const a = props.announcement
  return [
    a.author?.nickname,
    t('spaces.announcements.publishedOn', { day: dayText(announcementDay(a.createdAt)) }),
    a.updatedAt > a.createdAt ? t('spaces.announcements.edited') : '',
  ]
    .filter(Boolean)
    .join(' · ')
})
</script>

<template>
  <article class="acard" :class="{ 'acard--expired': expired }">
    <div class="acard__head">
      <span v-if="announcement.pinned && !expired" class="acard__pin">
        <v-icon icon="mdi-pin-outline" size="12" />{{ t('spaces.announcements.pinnedTag') }}
      </span>
      <h3 class="acard__title t-title">{{ announcement.title }}</h3>
    </div>
    <TipTapViewer v-if="announcement.content" class="acard__body t-body-readable" :value="announcement.content" />
    <div class="acard__meta t-meta-read">
      <span>{{ byline }}</span>
      <span v-if="announcement.expiresAt !== null">
        {{ t('spaces.announcements.expiresOn', { day: dayText(expiryDay(announcement.expiresAt)) }) }}
      </span>
      <span v-if="manager" class="acard__ops">
        <BaseButton
          v-if="!expired"
          kind="ghost"
          :icon="announcement.pinned ? 'mdi-pin-off-outline' : 'mdi-pin-outline'"
          :aria-label="t(announcement.pinned ? 'spaces.announcements.unpin' : 'spaces.announcements.pin')"
          :title="t(announcement.pinned ? 'spaces.announcements.unpin' : 'spaces.announcements.pin')"
          size="sm"
          @click="$emit('pin')"
        />
        <BaseButton
          kind="ghost"
          icon="mdi-pencil-outline"
          :aria-label="t('spaces.announcements.edit')"
          :title="t('spaces.announcements.edit')"
          size="sm"
          @click="$emit('edit')"
        />
        <BaseButton
          kind="ghost"
          icon="mdi-delete-outline"
          :aria-label="t('spaces.announcements.delete')"
          :title="t('spaces.announcements.delete')"
          size="sm"
          @click="$emit('remove')"
        />
      </span>
    </div>
  </article>
</template>

<style scoped>
.acard {
  padding: 16px 18px;
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  background: var(--surface);
}

.acard--expired {
  color: var(--muted);
}

.acard__head {
  display: flex;
  gap: 8px;
  align-items: center;
  min-width: 0;
}

.acard__title {
  margin: 0;
  color: var(--ink);
}

.acard--expired .acard__title {
  color: var(--muted);
}

.acard__pin {
  display: inline-flex;
  flex-shrink: 0;
  gap: 3px;
  align-items: center;
  padding: 0 6px;
  border-radius: var(--radius-sm);
  background: var(--warn-wash);
  color: var(--warn-ink);
  font-size: 12px;
  font-weight: 600;
  line-height: var(--lh-12);
}

.acard__body {
  margin-top: 8px;
}

.acard__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 12px;
  align-items: center;
  margin-top: 12px;
}

.acard__ops {
  display: flex;
  gap: 4px;
  margin-left: auto;
}
</style>
