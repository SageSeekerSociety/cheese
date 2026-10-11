<script setup lang="ts">
// 上手三步：贴在项目「综合」输入框上方，三步做完就不再出现。新手引导建好项目后人落
// 在这里，它接着带：交给芝士第一件事、看芝士的计划、采纳第一份交付。
//
// 每一步打不打勾由服务端的事实决定（`composables/useStartSteps.ts` 问来，经 props
// 传进来），这个组件只管画。不圈按钮、不盖住界面；嫌占地方可以收起成一行，收起记在
// 这个浏览器里。亲手采纳过交付的人（`learned`）在哪个项目都看不到它。
import type { StartStepsState } from '@/composables/useStartSteps'

import { computed, ref, watch } from 'vue'

import { t } from '@/i18n'

const props = defineProps<{
  projectId: string
  steps: StartStepsState | null
}>()

const KEYS = ['asked', 'answered', 'accepted'] as const

const storageKey = computed(() => `cheese.startSteps.hidden:${props.projectId}`)
const hidden = ref(read(storageKey.value))
watch(storageKey, (key) => (hidden.value = read(key)))

function read(key: string): boolean {
  try {
    return localStorage.getItem(key) === '1'
  } catch {
    return false
  }
}
function setHidden(value: boolean) {
  hidden.value = value
  try {
    if (value) localStorage.setItem(storageKey.value, '1')
    else localStorage.removeItem(storageKey.value)
  } catch {
    // 存不下只是下次不记得收起，不影响三步本身。
  }
}

// 亲手采纳过交付的人已经会了，哪个项目都不再带。
const current = computed(() =>
  props.steps && !props.steps.learned ? KEYS.find((k) => !props.steps![k]) ?? null : null
)
</script>

<template>
  <section v-if="steps && current" class="start-steps" :aria-label="t('welcome.steps.title')">
    <button v-if="hidden" type="button" class="start-steps__pill" @click="setHidden(false)">
      {{ t('welcome.steps.show') }} · {{ KEYS.filter((k) => steps![k]).length }}/3
    </button>
    <template v-else>
      <div class="start-steps__head">
        <span class="t-title">{{ t('welcome.steps.title') }}</span>
        <button type="button" class="start-steps__hide" @click="setHidden(true)">{{ t('welcome.steps.hide') }}</button>
      </div>
      <ol class="start-steps__list">
        <li
          v-for="key in KEYS"
          :key="key"
          :class="{ done: steps[key], now: key === current }"
          :aria-current="key === current ? 'step' : undefined"
        >
          <span class="start-steps__mark" aria-hidden="true">
            <v-icon v-if="steps[key]" icon="mdi-check" size="12" />
          </span>
          <span>
            <span class="start-steps__label">{{ t(`welcome.steps.${key}`) }}</span>
            <span v-if="key === current" class="start-steps__hint">{{ t(`welcome.steps.${key}Hint`) }}</span>
          </span>
        </li>
      </ol>
    </template>
  </section>
</template>

<style scoped>
.start-steps {
  margin: 0 16px 8px;
  padding: 10px 12px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-md);
  background: var(--surface);
}
.start-steps:has(.start-steps__pill) {
  padding: 0;
  border: 0;
  background: transparent;
}
.start-steps__head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.start-steps__hide,
.start-steps__pill {
  color: var(--muted);
  font-size: 12px;
  cursor: pointer;
}
.start-steps__pill {
  padding: 2px 10px;
  border-radius: var(--radius-pill);
  background: var(--fill);
}
.start-steps__list {
  list-style: none;
  margin: 8px 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.start-steps__list li {
  display: flex;
  gap: 8px;
  align-items: flex-start;
  color: var(--muted);
}
.start-steps__list li.now {
  color: var(--ink);
}
.start-steps__list li.done .start-steps__label {
  color: var(--faint);
}
.start-steps__mark {
  flex: none;
  width: 16px;
  height: 16px;
  margin-top: 2px;
  border-radius: 50%;
  border: 1.5px solid var(--line-2);
  display: grid;
  place-items: center;
}
.start-steps__list li.now .start-steps__mark {
  border-color: var(--ink);
}
.start-steps__list li.done .start-steps__mark {
  background: var(--ok);
  border-color: var(--ok);
  color: var(--surface);
}
.start-steps__label {
  display: block;
}
.start-steps__hint {
  display: block;
  color: var(--faint);
  font-size: 12.5px;
}
</style>
