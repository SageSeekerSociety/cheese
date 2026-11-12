<script setup lang="ts">
// The product's composer, put together the way RoomComposer puts it together:
// the chip row (回复谁), the editor, and the actions row with ＋, the mic, the
// 交给芝士 face and 发送. Only the room's back end is missing, so the box
// is assembled here from the same parts instead of mounting RoomComposer.
import { computed } from 'vue'

import { REFS } from './shapes'
import { toast } from './state'

import ComposerActions from '@/components/room/ComposerActions.vue'
import ComposerChipRow from '@/components/room/ComposerChipRow.vue'
import ComposerEditor from '@/components/room/ComposerEditor.vue'
import ComposerSummon from '@/components/room/ComposerSummon.vue'

const props = defineProps<{
  placeholder: string
  replyLabel?: string | null
}>()
const text = defineModel<string>({ required: true })
const emit = defineEmits<{ (e: 'send'): void; (e: 'clear-reply'): void }>()

const CHEESE = { handle: 'cheese', label: '芝士' }
const on = computed(() => text.value.includes('<@cheese>'))
const canSend = computed(() => text.value.trim().length > 0)

function toggleSummon() {
  text.value = on.value ? text.value.replace(/<@cheese>\s?/g, '') : `<@cheese> ${text.value.replace(/^\s+/, '')}`
}

function onKey(e: KeyboardEvent) {
  if (e.key !== 'Enter' || e.shiftKey || e.isComposing) return
  e.preventDefault()
  if ((e.metaKey || e.ctrlKey) && !on.value) toggleSummon()
  if (canSend.value) emit('send')
}
</script>

<template>
  <div class="composer ot-composer">
    <div class="composer-box ot-composer__box">
      <ComposerChipRow
        :reply-label="props.replyLabel ?? null"
        :quote="null"
        :atts="[]"
        topic-id="demo"
        @clear-reply="emit('clear-reply')"
      />
      <ComposerEditor
        v-model="text"
        class="composer-input"
        :names="REFS"
        :placeholder="placeholder"
        :label="placeholder"
        title="回车发送，Shift+回车换行，⌘/Ctrl+回车交给芝士"
        @keydown="onKey"
      />
      <ComposerActions
        :uploading="false"
        :can-send="canSend"
        :show-image-picker="false"
        :enter-sends="true"
        :can-checklist="true"
        :can-remind="true"
        :can-voice="true"
        @files="toast('文件会传到这个频道，演示里不上传')"
        @checklist="toast('清单：一步一步列出要做的事')"
        @remind="toast('提醒：到点在这里提醒你')"
        @voice="(how: string) => how === 'tap' && toast('语音输入要用麦克风，演示里没开')"
        @send="emit('send')"
      >
        <template #summon>
          <ComposerSummon
            :mates="[CHEESE]"
            :shown="[CHEESE]"
            :on="on"
            :fixed="false"
            :ready="true"
            :busy="[]"
            @toggle="toggleSummon"
            @pick="toggleSummon"
            @off="text = text.replace(/<@cheese>\s?/g, '')"
          />
        </template>
      </ComposerActions>
    </div>
  </div>
</template>

<style scoped>
.composer {
  position: relative;
  flex: none;
  padding: 8px 16px 12px;
  background: var(--surface);
}

.composer-box {
  padding: 4px 6px 4px 10px;
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  transition:
    border-color var(--dur-quick) var(--ease-standard),
    box-shadow var(--dur-quick) var(--ease-standard);
}

.composer-box:focus-within {
  border-color: var(--faint);
}
</style>
