<script setup lang="ts">
// 「改动」那一格的 ⋯：偶尔才换的那几样（看哪个版本、下载打开的这份、刷新）。
//
// 它在两处出现：全部改动那一面的文件清单那一行（顶部那块还在屏幕上时）、滚过顶部之后
// 那条细栏，以及单独打开一份文件时的横条。三处是同一组选项，所以写一次。桌面上是一个
// 分了组的下拉菜单，手机上是底部面板。
import type { FileSource, RoomTask } from '../../cx_types'
import type { MenuAction } from '../common/menuAction'

import { computed, ref } from 'vue'
import { useDisplay } from 'vuetify'

import MobileActionSheet from '../common/MobileActionSheet.vue'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

const props = defineProps<{
  currentTask: RoomTask | undefined
  fileSource: FileSource
  /** 有一份打开着的文件可以下载。 */
  canDownload: boolean
  refreshing: boolean
}>()

const emit = defineEmits<{
  (e: 'select-version', source: FileSource): void
  (e: 'download'): void
  (e: 'refresh'): void
}>()

const { mdAndUp } = useDisplay()

// 手机上的底部面板：同一组选项，版本是二选一，选中的那一项画成实心的圆。
const sheetOpen = ref(false)
const sheetActions = computed<MenuAction[]>(() => {
  const pick = (on: boolean) => (on ? 'mdi-radiobox-marked' : 'mdi-radiobox-blank')
  const list: MenuAction[] = []
  if (props.currentTask?.status === 'open') {
    const live = props.fileSource === 'live'
    list.push(
      {
        key: 'source-live',
        label: t('work.room.changes.liveFile'),
        icon: pick(live),
        onSelect: () => emit('select-version', 'live'),
      },
      {
        key: 'source-committed',
        label: t('work.room.changes.committedVersion'),
        icon: pick(!live),
        onSelect: () => emit('select-version', 'committed'),
      }
    )
  }
  if (props.canDownload) {
    list.push({
      key: 'download',
      label: t('work.room.changes.download'),
      icon: 'mdi-download-outline',
      onSelect: () => emit('download'),
    })
  }
  list.push({
    key: 'refresh',
    label: t('work.room.changes.refresh'),
    icon: 'mdi-refresh',
    loading: props.refreshing,
    onSelect: () => emit('refresh'),
  })
  return list
})
</script>

<template>
  <template v-if="!mdAndUp">
    <BaseButton
      kind="ghost"
      icon="mdi-dots-horizontal"
      size="sm"
      class="tap-target"
      :title="t('work.room.changes.more')"
      :aria-label="t('work.room.changes.more')"
      :loading="refreshing"
      @click="sheetOpen = true"
    />
    <MobileActionSheet v-model="sheetOpen" :actions="sheetActions" />
  </template>
  <v-menu v-else location="bottom end">
    <template #activator="{ props: menuProps }">
      <BaseButton
        v-bind="menuProps"
        kind="ghost"
        icon="mdi-dots-horizontal"
        size="sm"
        :title="t('work.room.changes.more')"
        :aria-label="t('work.room.changes.more')"
        :loading="refreshing"
      />
    </template>
    <v-list density="compact" role="menu" :aria-label="t('work.room.changes.options')">
      <template v-if="currentTask?.status === 'open'">
        <v-list-subheader class="pt-0">{{ t('work.room.changes.version') }}</v-list-subheader>
        <v-list-item
          role="menuitem"
          :title="t('work.room.changes.liveFile')"
          :subtitle="t('work.room.changes.liveNote')"
          :active="fileSource === 'live'"
          @click="emit('select-version', 'live')"
        />
        <v-list-item
          role="menuitem"
          :title="t('work.room.changes.committedVersion')"
          :subtitle="t('work.room.changes.committedNote')"
          :active="fileSource === 'committed'"
          @click="emit('select-version', 'committed')"
        />
        <v-divider class="my-1" />
      </template>
      <v-list-item
        v-if="canDownload"
        role="menuitem"
        :title="t('work.room.changes.download')"
        prepend-icon="mdi-download-outline"
        @click="emit('download')"
      />
      <v-list-item
        role="menuitem"
        :title="t('work.room.changes.refresh')"
        prepend-icon="mdi-refresh"
        @click="emit('refresh')"
      />
    </v-list>
  </v-menu>
</template>
