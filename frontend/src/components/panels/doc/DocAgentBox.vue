<script setup lang="ts">
// 点 AI 队友开出的输入框：上面是输入框，一打开就能打字；下面是常用的说法，分「修改」
// 「提问」两组。打字时下面跟着筛：对得上的那一项被选中，回车就是它；一项都对不上，
// 回车把这句话交给它。方向键在列表里上下。点一项就做，不再确认。
import type { AgentPreset, AgentScope, PresetContext } from '../../../lib/docAgent'

import { computed, onMounted, ref, watch } from 'vue'

import { useFocusReturn } from '@/composables/useFocusReturn'

import { matching, presetsFor } from '../../../lib/docAgent'
import CheeseAvatar from '../../CheeseAvatar.vue'

import { t } from '@/i18n'

const props = defineProps<{ agentName: string; scope: AgentScope; context: PresetContext }>()
const emit = defineEmits<{
  (e: 'run', preset: AgentPreset, label: string): void
  (e: 'say', text: string): void
  (e: 'cancel'): void
}>()

const text = ref('')
const input = ref<HTMLInputElement | null>(null)
const name = (p: AgentPreset) => t(p.label)
const groups = computed(() => matching(presetsFor(props.scope, props.context), text.value, name))
const flat = computed(() => [...groups.value.edit, ...groups.value.ask])
const active = ref(0)
watch(flat, () => (active.value = 0))
const asksOnly = computed(() => !props.context.editable)

onMounted(() => input.value?.focus({ preventScroll: true }))

// 取消（这一层卸载）时把焦点还回点开它的那一处。
useFocusReturn(ref(true))

function choose(preset: AgentPreset) {
  emit('run', preset, name(preset))
}
function submit() {
  const picked = flat.value[active.value]
  if (picked) choose(picked)
  else if (text.value.trim()) emit('say', text.value.trim())
}
function onKey(e: KeyboardEvent) {
  if (e.isComposing) return
  if (e.key === 'Escape') {
    e.preventDefault()
    emit('cancel')
  } else if (e.key === 'Enter') {
    e.preventDefault()
    submit()
  } else if (e.key === 'ArrowDown' && flat.value.length) {
    e.preventDefault()
    active.value = (active.value + 1) % flat.value.length
  } else if (e.key === 'ArrowUp' && flat.value.length) {
    e.preventDefault()
    active.value = (active.value - 1 + flat.value.length) % flat.value.length
  }
}
const index = (p: AgentPreset) => flat.value.indexOf(p)
</script>

<template>
  <div class="doc-agent-box" role="dialog" :aria-label="agentName">
    <div class="doc-agent-box__field">
      <CheeseAvatar :size="18" :name="agentName" />
      <input
        ref="input"
        v-model="text"
        class="doc-agent-box__input"
        autocomplete="off"
        role="combobox"
        aria-autocomplete="list"
        :aria-expanded="flat.length > 0"
        :aria-label="asksOnly ? t('work.room.docAgent.askPlaceholder') : t('work.room.docAgent.placeholder')"
        :placeholder="asksOnly ? t('work.room.docAgent.askPlaceholder') : t('work.room.docAgent.placeholder')"
        @keydown="onKey"
      />
      <button
        v-if="text.trim() && !flat.length"
        type="button"
        class="doc-agent-box__send"
        :aria-label="t('work.room.docAgent.send')"
        @click="emit('say', text.trim())"
      >
        <v-icon size="16">mdi-arrow-up</v-icon>
      </button>
    </div>
    <div v-if="flat.length" class="doc-agent-box__list" role="listbox">
      <template v-for="group in ['edit', 'ask'] as const" :key="group">
        <template v-if="groups[group].length">
          <div class="doc-agent-box__group">
            {{ group === 'edit' ? t('work.room.docAgent.groupEdit') : t('work.room.docAgent.groupAsk') }}
          </div>
          <button
            v-for="preset in groups[group]"
            :key="preset.id"
            type="button"
            role="option"
            class="doc-agent-box__option"
            :aria-selected="index(preset) === active"
            @mouseenter="active = index(preset)"
            @click="choose(preset)"
          >
            <v-icon size="16">{{ preset.icon }}</v-icon>
            {{ name(preset) }}
          </button>
        </template>
      </template>
    </div>
  </div>
</template>

<style scoped>
.doc-agent-box {
  box-sizing: border-box;
  width: 100%;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-lg);
  background: var(--raised);
  box-shadow: var(--shadow-2);
}
.doc-agent-box__field {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
}
.doc-agent-box__list {
  padding: 0 6px 6px;
  border-top: 1px solid var(--line);
}
.doc-agent-box__input {
  flex: 1 1 auto;
  min-width: 0;
  border: 0;
  outline: 0;
  background: transparent;
  color: var(--text);
  font: inherit;
  font-size: 14px;
  line-height: var(--lh-14);
}
.doc-agent-box__input::placeholder {
  color: var(--faint);
}
.doc-agent-box__send {
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  border-radius: var(--radius-sm);
  background: var(--ink);
  color: var(--surface);
}
.doc-agent-box__send:focus-visible,
.doc-agent-box__option:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: 1px;
}
.doc-agent-box__group {
  padding: 8px 12px 4px;
  color: var(--faint);
  font-size: 12px;
  line-height: var(--lh-12);
}
.doc-agent-box__option {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  height: 34px;
  padding: 0 12px;
  border-radius: var(--radius-md);
  color: var(--text);
  font-size: 14px;
  line-height: var(--lh-14);
  text-align: left;
  transition: background var(--dur-quick) var(--ease-standard);
}
.doc-agent-box__option[aria-selected='true'] {
  background: var(--fill);
}
.doc-agent-box__option :deep(.v-icon) {
  color: var(--muted);
}
</style>
