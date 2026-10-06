<script setup lang="ts">
// 频道概览里的「置顶」：主线上钉住的消息和文件，后钉的在前。点一条回到它在主线上的
// 位置；⋯ 里取消置顶。没有置顶就整段不画。
import type { MenuAction } from '@/components/common/menuAction'
import type { RefNames } from '@/lib/refChip'
import type { ChannelPin } from '@/types/channels'

import BaseButton from '@/components/base/BaseButton.vue'
import AdaptiveMenu from '@/components/common/AdaptiveMenu.vue'
import { t } from '@/i18n'
import { replySnippet } from '@/lib/blockDisplay'
import { relTime } from '@/lib/relTime'

const props = defineProps<{
  pins: ChannelPin[]
  canPin: boolean
  memberNames: Record<string, string>
  refs: RefNames
}>()

const emit = defineEmits<{
  (e: 'unpin', blockId: string): void
  (e: 'jump', blockId: string): void
}>()

const nameOf = (handle: string) => props.memberNames[handle] || handle

function isFile(pin: ChannelPin): boolean {
  return pin.block.kind !== 'message'
}
function fileName(path: string): string {
  return path.split('/').pop() || path
}
function actions(pin: ChannelPin): MenuAction[] {
  const out: MenuAction[] = [
    {
      key: 'jump',
      label: t('work.channel.pins.jump'),
      icon: 'mdi-arrow-right',
      onSelect: () => emit('jump', pin.block.id),
    },
  ]
  if (props.canPin)
    out.push({
      key: 'unpin',
      label: t('work.channel.pins.unpin'),
      icon: 'mdi-pin-off-outline',
      onSelect: () => emit('unpin', pin.block.id),
    })
  return out
}
</script>

<template>
  <section v-if="pins.length" class="pins" data-testid="channel-pins">
    <h3 class="pins__title">{{ t('work.channel.pins.title', { count: pins.length }) }}</h3>
    <div v-for="pin in pins" :key="pin.block.id" class="pin">
      <button type="button" class="pin__body" @click="emit('jump', pin.block.id)">
        <template v-if="isFile(pin)">
          <v-icon size="16" class="pin__icon">mdi-paperclip</v-icon>
          <span class="pin__text">
            <span class="t-body pin__name">{{ fileName(pin.block.content) }}</span>
            <span class="t-meta c-faint">{{
              t('work.channel.pins.pinnedBy', { name: nameOf(pin.pinned_by), when: relTime(pin.pinned_at) })
            }}</span>
          </span>
        </template>
        <span v-else class="pin__text">
          <span class="t-meta c-faint">{{
            t('work.channel.pins.said', { name: nameOf(pin.block.author), when: relTime(pin.block.created_at) })
          }}</span>
          <span class="t-body pin__said">{{ replySnippet(pin.block, refs, 200) }}</span>
        </span>
      </button>
      <AdaptiveMenu :actions="actions(pin)" :title="t('work.channel.pins.menu')">
        <template #activator="{ props: menuProps }">
          <BaseButton
            v-bind="menuProps"
            icon="mdi-dots-horizontal"
            size="sm"
            class="tap-target"
            :aria-label="t('work.channel.pins.menu')"
          />
        </template>
      </AdaptiveMenu>
    </div>
  </section>
</template>

<style scoped>
.pins {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.pins__title {
  margin: 0;
  font-size: 13px;
  line-height: var(--lh-13);
  font-weight: 600;
  color: var(--ink);
}
.pin {
  display: flex;
  align-items: flex-start;
  gap: 4px;
  padding: 4px 4px 4px 10px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
}
.pin__body {
  display: flex;
  flex: 1 1 auto;
  align-items: center;
  gap: 8px;
  min-width: 0;
  padding: 4px 0;
  border: 0;
  background: none;
  text-align: left;
  cursor: pointer;
}
.pin__icon {
  flex: none;
  color: var(--muted);
}
.pin__text {
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.pin__name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text);
}
.pin__said {
  display: -webkit-box;
  overflow: hidden;
  -webkit-line-clamp: 3;
  line-clamp: 3;
  -webkit-box-orient: vertical;
  color: var(--text);
}
</style>
