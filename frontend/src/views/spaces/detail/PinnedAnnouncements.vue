<script setup lang="ts">
// 题目列表顶上那一栏：这个空间置顶、还没到期的公告，一条一行，点开去公告页。一条
// 都没有时整块不出现。手机上只列第一条，其余的收成一行「另有 N 条置顶公告」。
//
// 只认 props：读公告、算去哪都在容器里（Tasks.vue 用 useSpaceAnnouncements 取当前
// 公告、把公告页地址算好传进来）。这样这一栏在没装路由、没连 API 的树里也能渲染。
import type { UserRefTarget } from '@/lib/userRef'
import type { SpaceAnnouncement } from '@/types'

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useDisplay } from 'vuetify'

import { announcementDay, expiryDay } from '../model'

import { useAnnouncementDay } from './useAnnouncementDay'

const props = defineProps<{
  /** 这个空间「当前」的公告。哪些已到期由服务端分好，这里不自己比时间。 */
  current: SpaceAnnouncement[]
  /** 公告页地址。空的话点了不去。 */
  target: UserRefTarget
}>()

const { t } = useI18n()
const { smAndDown } = useDisplay()
const dayText = useAnnouncementDay()

const pinned = computed(() => props.current.filter((a) => a.pinned))
const shown = computed(() => (smAndDown.value ? pinned.value.slice(0, 1) : pinned.value))
const more = computed(() => pinned.value.length - shown.value.length)

function when(createdAt: number, expiresAt: number | null): string {
  const published = dayText(announcementDay(createdAt))
  return expiresAt === null
    ? published
    : `${published} · ${t('spaces.announcements.expiresOn', { day: dayText(expiryDay(expiresAt)) })}`
}
</script>

<template>
  <nav v-if="pinned.length" class="pinned" :aria-label="t('spaces.detail.announcements')">
    <router-link v-for="a in shown" :key="a.id" :to="target" class="pinned__row t-body">
      <span class="pinned__tag"
        ><v-icon icon="mdi-pin-outline" size="12" />{{ t('spaces.announcements.pinnedTag') }}</span
      >
      <span class="pinned__title">{{ a.title }}</span>
      <span class="pinned__when t-meta-read">{{ when(a.createdAt, a.expiresAt) }}</span>
    </router-link>
    <router-link v-if="more > 0" :to="target" class="pinned__row pinned__more">
      {{ t('spaces.announcements.morePinned', { count: more }) }}
      <v-icon icon="mdi-chevron-right" size="18" class="pinned__chev" />
    </router-link>
  </nav>
</template>

<style scoped>
.pinned {
  overflow: hidden;
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  background: var(--surface);
}

.pinned__row {
  display: flex;
  gap: 10px;
  align-items: center;
  min-height: 44px;
  padding: 0 16px;
  border-top: 1px solid var(--line);
  color: var(--ink);
  text-decoration: none;
}

.pinned__row:first-child {
  border-top: none;
}

.pinned__row:hover {
  background: var(--fill);
}

.pinned__tag {
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

.pinned__title {
  overflow: hidden;
  min-width: 0;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pinned__when {
  flex-shrink: 0;
  margin-left: auto;
  white-space: nowrap;
}

.pinned__more {
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
}

.pinned__chev {
  margin-left: auto;
}

/* 手机上一行放不下标题和日期：日期换到标题下面。 */
@media (max-width: 959px) {
  .pinned__row:not(.pinned__more) {
    flex-wrap: wrap;
    gap: 4px 6px;
    padding: 10px 14px;
  }

  .pinned__when {
    flex-basis: 100%;
    margin-left: 0;
  }
}
</style>
