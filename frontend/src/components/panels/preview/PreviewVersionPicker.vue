<script setup lang="ts">
// 文档条上文件名旁那一行「第 N 版 · 谁 · 什么时候」，点开是这份文件的每一版。
//
// 读者最常问的两件事是「这是不是最新的」「谁刚改过」，所以它常驻在条上，不藏进历史栏。
// 点开换版：当前那一版回到正文；旧版画得出来就看它，画不出来（要服务器转换的 Word、
// 演示稿）就拿它和当前版比——这件事由取数那一层决定（`composables/panelPreview/versions.ts`
// 的 `choose`），这里只报点了哪一版。下载、恢复仍在保存历史那一栏里，菜单底下有一条去处。
//
// 窄的时候（右栏、手机）只写「第 N 版」：谁、什么时候进 title 和菜单里，文档条不能被
// 撑成两行。
import type { VersionEntry } from '@/composables/panelPreview/versions'

import { computed } from 'vue'

import { t } from '@/i18n'
import { absDateTime } from '@/lib/dateText'
import { relTime } from '@/lib/relTime'

const props = defineProps<{
  list: readonly VersionEntry[]
  current: VersionEntry
  /** 屏幕上正在看的是哪一版（看旧版时不是当前那一版）。 */
  shown: number
  compact?: boolean
  /** 点一版之后会怎样：直接看旧版、只能拿它比、还是只能去历史里下载。 */
  onPick: 'view' | 'compare' | 'none'
}>()

const emit = defineEmits<{
  choose: [seq: number]
  history: []
}>()

const meta = computed(() =>
  t('work.room.versions.meta', { who: props.current.author, when: relTime(props.current.at) })
)
const title = computed(() =>
  t('work.room.versions.switchTitle', {
    seq: props.current.seq,
    who: props.current.author,
    when: absDateTime(new Date(props.current.at).getTime()),
  })
)
</script>

<template>
  <v-menu location="bottom start" :max-width="360">
    <template #activator="{ props: menuProps }">
      <button
        v-bind="menuProps"
        type="button"
        class="vp"
        :title="title"
        :aria-label="title"
        data-testid="version-picker"
      >
        <span class="vp__seq">{{ t('work.room.versions.label', { seq: current.seq }) }}</span>
        <span v-if="!compact" class="vp__meta">· {{ meta }}</span>
        <v-icon size="14" class="vp__chev">mdi-chevron-down</v-icon>
      </button>
    </template>
    <v-list density="compact" class="vp__list" role="menu" :aria-label="t('work.room.versions.menuTitle')">
      <v-list-subheader>{{ t('work.room.versions.menuTitle') }}</v-list-subheader>
      <v-list-item
        v-for="entry in list"
        :key="entry.id"
        role="menuitem"
        :active="entry.seq === shown"
        :disabled="onPick === 'none' && !entry.current"
        :data-seq="entry.seq"
        @click="emit('choose', entry.seq)"
      >
        <div class="vp__row">
          <strong>{{ t('work.room.versions.label', { seq: entry.seq }) }}</strong>
          <span class="t-meta">{{ entry.author }} · {{ relTime(entry.at) }}</span>
          <v-chip v-if="entry.latest" size="x-small" color="primary" variant="tonal">{{
            t('work.room.versions.latest')
          }}</v-chip>
          <v-chip v-if="entry.lastSeen" size="x-small" variant="tonal">{{ t('work.room.versions.lastSeen') }}</v-chip>
        </div>
        <div v-if="entry.note" class="vp__note t-meta">{{ entry.note }}</div>
      </v-list-item>
      <div v-if="onPick !== 'view' && list.length > 1" class="vp__foot t-meta">
        {{ onPick === 'compare' ? t('work.room.versions.clickToCompare') : t('work.room.versions.clickToDownload') }}
      </div>
      <v-divider />
      <v-list-item role="menuitem" prepend-icon="mdi-history" data-testid="file-history" @click="emit('history')">
        <v-list-item-title>{{ t('work.room.versions.allHistory') }}</v-list-item-title>
      </v-list-item>
    </v-list>
  </v-menu>
</template>

<style scoped>
/* 一行字，不是一颗按钮的样子：和文件名同一行读下去，悬停才看出点得开。 */
/* 让位时只收作者和时间，「第 N 版」和箭头始终留着：最窄也是一行能认出的版本号，
   不会被旁边的按钮压成一条叠字的缝。8em 放得下英文的「Version 12」连箭头。 */
.vp {
  flex: 0 1 auto;
  min-width: 8em;
  overflow: hidden;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 26px;
  padding: 0 6px;
  border-radius: var(--radius-sm);
  font-size: 12px;
  color: var(--muted);
  white-space: nowrap;
  transition: background-color var(--dur-quick) var(--ease-standard);
}
.vp:hover,
.vp[aria-expanded='true'] {
  background: var(--fill);
  color: var(--ink);
}
.vp__seq {
  flex: none;
}
.vp__meta {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}
.vp__chev {
  flex: none;
  color: var(--faint);
}
.vp__list {
  max-height: 420px;
  overflow: auto;
}
.vp__row {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  font-size: 13px;
}
.vp__note {
  margin-top: 2px;
  white-space: pre-wrap;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.vp__foot {
  padding: 4px 16px 8px;
}
</style>
