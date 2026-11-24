<script setup lang="ts">
// 跟随: the button in the editor's bar that sends the editor wherever the AI teammate is changing
// a file. Pressed while following; a dot beside the face says the teammate is changing a file
// now and the editor is not following it. Clicking toggles; acting in the editor ends it too
// (the page's business).
import { computed } from 'vue'

import { t } from '../../i18n'

const props = defineProps<{
  /** The teammate's name, as its face and the button's title say it. */
  name: string
  /** Its avatar colour: a fixed-lightness hex, the same in both themes (`avatarColor`). */
  color: string
  on: boolean
  /** The teammate is changing a file now. */
  editing: boolean
}>()
const emit = defineEmits<{ toggle: [] }>()

const title = computed(() =>
  props.on
    ? t('work.room.follow.stop')
    : props.editing
      ? t('work.room.follow.editing', { name: props.name })
      : t('work.room.follow.start', { name: props.name })
)
</script>

<template>
  <button
    type="button"
    class="follow"
    :class="{ 'follow--on': on }"
    :aria-pressed="on"
    :title="title"
    data-follow
    @click="emit('toggle')"
  >
    <!-- The face is the teammate's avatar colour, the same in both themes, so its ink is too
         (--avatar-ink). -->
    <span class="follow__face" :style="{ background: color }" aria-hidden="true">{{ name.slice(0, 1) }}</span>
    <span>{{ t('work.room.follow.label') }}</span>
    <span v-if="editing && !on" class="follow__live" :style="{ background: color }" aria-hidden="true" />
  </button>
</template>

<style scoped>
.follow {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 26px;
  padding: 0 8px 0 4px;
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
  cursor: pointer;
  transition:
    background-color var(--dur-quick) var(--ease-standard),
    color var(--dur-quick) var(--ease-standard);
}
.follow:hover {
  background: var(--fill);
  color: var(--text);
}
.follow--on {
  background: var(--fill-2);
  color: var(--ink);
  font-weight: 600;
}
.follow__face {
  display: grid;
  place-items: center;
  width: 18px;
  height: 18px;
  border-radius: var(--radius-pill);
  color: var(--avatar-ink);
  font-size: 12px;
  font-weight: 600;
}
.follow__live {
  width: 6px;
  height: 6px;
  border-radius: var(--radius-pill);
  animation: follow-live 1.6s ease-in-out infinite;
}
@keyframes follow-live {
  50% {
    opacity: 0.35;
  }
}
@media (prefers-reduced-motion: reduce) {
  .follow__live {
    animation: none;
  }
}
</style>
