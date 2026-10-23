<script setup lang="ts">
// 面板顶上那一栏：放大镜（手机上换成返回）、范围 chip、输入框、搜索进度条。
//
// 「在面板里问一句」（重命名一个话题）的时候，这一栏换成那一句的答案 —— 还是同一个
// 焦点位置，所以两套输入框都在这里，出现哪一个由 ask 决定。什么时候把焦点送进来由
// 父级说了算（开门、换范围、从操作退回结果列表），所以这里只管「把焦点放到此刻那个
// 输入框上」，位置和时机分开，两边都不必知道对方。
import type { PaletteAsk } from './state'

import { ref } from 'vue'

import { resultsId } from './ids'

import BaseButton from '@/components/base/BaseButton.vue'
import { t } from '@/i18n'

defineProps<{
  /** 桌面上这一栏在面板顶上是浮着的；手机上面板是一整页，返回键占放大镜的位置。 */
  mdAndUp: boolean
  /** 面板在问的一句话。有话要问的时候不搜东西。 */
  ask: PaletteAsk | null
  /** 现在只在一个项目里找的话，那个项目的名字；没进范围就是空的。 */
  scopeName: string | undefined
  /** 正在问远程数据源：进度条这时候亮。 */
  searching: boolean
  /** 下面有结果：输入框上的 aria-expanded。 */
  expanded: boolean
  /** aria-activedescendant 指过去的那一格。 */
  activeDescendant: string | undefined
}>()

const emit = defineEmits<{
  /** 面板自己关掉。 */
  close: []
  /** 结果列表上的按键：方向键、回车、Tab、Esc 都归父级判。 */
  keydown: [event: KeyboardEvent]
  /** 问那一句的输入框上的按键：回车交答案，Esc 不改了。 */
  askKeydown: [event: KeyboardEvent]
  /** 点范围 chip：去掉项目范围，跨项目只找名字。 */
  leaveScope: []
}>()

const query = defineModel<string>('input', { required: true })
const answer = defineModel<string>('askText', { required: true })

// 两套输入框挂同一个名字：同一时刻只画得出来一个。
const field = ref<HTMLInputElement | null>(null)
defineExpose({ focus: () => field.value?.focus(), select: () => field.value?.select() })
</script>

<template>
  <div class="palette__input" :class="{ 'palette__input--page': !mdAndUp }">
    <BaseButton
      v-if="!mdAndUp"
      icon="mdi-arrow-left"
      size="sm"
      class="tap-target"
      :aria-label="t('navigation.palette.close')"
      @click="emit('close')"
    />
    <v-icon v-else icon="mdi-magnify" size="20" class="palette__glass" />
    <template v-if="ask">
      <span class="palette__asking">{{ ask.title }}</span>
      <input
        ref="field"
        v-model="answer"
        type="text"
        autocomplete="off"
        spellcheck="false"
        :aria-label="ask.title"
        :placeholder="ask.placeholder"
        @keydown="emit('askKeydown', $event)"
      />
    </template>
    <button
      v-if="!ask && scopeName"
      type="button"
      class="palette__asking palette__scope"
      :title="t('navigation.palette.leaveScope')"
      :aria-label="t('navigation.palette.leaveScope')"
      @click="emit('leaveScope')"
    >
      {{ scopeName }}<v-icon icon="mdi-chevron-right" size="14" />
    </button>
    <input
      v-if="!ask"
      ref="field"
      v-model="query"
      type="text"
      role="combobox"
      autocomplete="off"
      spellcheck="false"
      aria-autocomplete="list"
      :aria-controls="resultsId"
      :aria-expanded="expanded"
      :aria-activedescendant="activeDescendant"
      :aria-label="t('navigation.palette.open')"
      :placeholder="t('navigation.palette.placeholder')"
      @keydown="emit('keydown', $event)"
    />
    <v-progress-linear :active="searching" indeterminate absolute location="bottom" height="2" color="primary" />
  </div>
</template>

<style scoped>
.palette__input {
  position: relative;
  display: flex;
  flex: none;
  align-items: center;
  gap: 8px;
  height: 52px;
  padding: 0 14px;
  border-bottom: 1px solid var(--line);
}
.palette__glass {
  color: var(--faint);
}
.palette__input input {
  flex: 1 1 auto;
  min-width: 0;
  height: 100%;
  border: 0;
  outline: 0;
  background: none;
  color: var(--ink);
  font-size: 15px;
  line-height: var(--lh-15);
}
.palette__input input::placeholder {
  color: var(--faint);
}
.palette__asking {
  flex: none;
  padding: 0 8px;
  border-radius: var(--radius-sm);
  background: var(--fill-2);
  color: var(--ink);
  font-size: 13px;
  line-height: var(--lh-13);
  white-space: nowrap;
}
.palette__scope {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  border: 0;
  cursor: pointer;
}

/* 手机上是一整页：这一栏钉在顶上，返回键替掉放大镜。 */
.palette__input--page {
  height: 56px;
  padding: 0 8px;
}
</style>
