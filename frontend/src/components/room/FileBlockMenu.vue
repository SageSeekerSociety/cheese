<script setup lang="ts">
// 时间线上一份文件（发来的附件、芝士摆出来的东西）旁边那颗 ⋯：打开、下载、存进资料库，
// 以及在频道主线上置顶到频道。有哪几项由放它的那一行决定，这里只把它们排成一份菜单。
import type { MenuAction } from '../common/menuAction'

import { computed, ref } from 'vue'

import AdaptiveMenu from '../common/AdaptiveMenu.vue'

import { t } from '@/i18n'

const props = defineProps<{
  name: string
  canOpen?: boolean
  canDownload?: boolean
  canKeep?: boolean
  /** 这一行在频道主线上、我能在那里说话：能置顶或取消置顶。 */
  pinnable?: boolean
  pinned?: boolean
}>()

const emit = defineEmits<{
  (e: 'open'): void
  (e: 'download'): void
  (e: 'keep'): void
  (e: 'pin'): void
  (e: 'unpin'): void
}>()

const open = ref(false)
const actions = computed<MenuAction[]>(() => {
  const list: MenuAction[] = []
  if (props.canOpen)
    list.push({ key: 'open', label: t('work.room.file.open'), icon: 'mdi-open-in-new', onSelect: () => emit('open') })
  if (props.canDownload)
    list.push({
      key: 'download',
      label: t('work.room.file.download'),
      icon: 'mdi-download-outline',
      onSelect: () => emit('download'),
    })
  if (props.canKeep)
    list.push({
      key: 'keep',
      label: t('work.room.file.keep'),
      icon: 'mdi-folder-plus-outline',
      onSelect: () => emit('keep'),
    })
  if (props.pinnable)
    list.push(
      props.pinned
        ? { key: 'unpin', label: t('work.room.pin.unpin'), icon: 'mdi-pin-off-outline', onSelect: () => emit('unpin') }
        : { key: 'pin', label: t('work.room.pin.pin'), icon: 'mdi-pin-outline', onSelect: () => emit('pin') }
    )
  return list
})
</script>

<template>
  <AdaptiveMenu v-if="actions.length" v-model="open" :actions="actions">
    <template #activator="{ props: menu }">
      <button
        v-bind="menu"
        type="button"
        class="file-menu"
        data-testid="file-menu"
        :title="t('work.room.file.more', { name })"
        :aria-label="t('work.room.file.more', { name })"
        @click.stop
      >
        <v-icon size="16">mdi-dots-horizontal</v-icon>
      </button>
    </template>
  </AdaptiveMenu>
</template>

<style scoped>
.file-menu {
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: 0;
  border-radius: var(--radius-md);
  background: transparent;
  color: var(--muted);
  cursor: pointer;
  transition: background-color var(--dur-quick) var(--ease-standard);
}
.file-menu:hover,
.file-menu[aria-expanded='true'] {
  background: var(--fill-2);
  color: var(--text);
}
</style>
