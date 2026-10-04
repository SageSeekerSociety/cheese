<script setup lang="ts">
import type { ShortcutGroup } from '@/components/common/shortcutSheet'

import { computed } from 'vue'

import ShortcutSheet from '@/components/common/ShortcutSheet.vue'
import { t } from '@/i18n'
import { statusMeta } from '@/lib/feedbackMeta'

/**
 * AdminShortcutSheet.vue — `?` 打开的那张快捷键表。
 *
 * 它是这套键盘分诊唯一的「说明书」：`j`/`k`/`1`/`2`/`3`/`H`/`U`/`A`/`M` 这些键在界面上
 * 没有别的地方能看见，用户要么记得住，要么就得有这张表。所以表本身必须**按作用域分组**：
 * 「这个键现在管不管用」取决于焦点在哪（列表 / 详情 / 全局），一张平铺的 19 行表读不出
 * 这层信息，分组标题能。
 *
 * 表里不含 §8 那列「守卫 / 备注」的原文：那一列一半是行为承诺（「到末行停住」）、一半
 * 是实现注记（函数名、接口路径）。后者绝不能上屏（文案规则：实现词不进 UI），前者留了
 * 几处真正会影响按键预期的，写在第三列的灰字里。
 */
defineOptions({ name: 'AdminShortcutSheet' })

defineProps<{ modelValue: boolean }>()

const emit = defineEmits<{ (e: 'update:modelValue', v: boolean): void }>()

/**
 * 动作里的状态名从 `statusMeta` 取，不照抄规格那几句：芯片上写的是「处理中 / 已修复 /
 * 已上线」，表上若写成「进行中 / 已解决」，用户在界面上找不到这几个字。键位是常量，
 * 状态名是会漂的，漂的那一份只能有一个源头。
 */
const groups = computed<ShortcutGroup[]>(() => [
  {
    scope: t('admin.shortcuts.scope.global'),
    rows: [
      { keys: ['/'], action: t('admin.shortcuts.focusSearch'), note: t('admin.shortcuts.notInInputs') },
      { keys: ['R'], action: t('admin.shortcuts.refresh') },
      { keys: ['G', 'Q'], sequence: true, action: t('admin.shortcuts.goQueue'), note: t('admin.shortcuts.within1s') },
      {
        keys: ['G', 'D'],
        sequence: true,
        action: t('admin.shortcuts.goDashboard'),
        note: t('admin.shortcuts.within1s'),
      },
      {
        keys: ['G', 'F'],
        sequence: true,
        action: t('admin.shortcuts.goFeedbackCenter'),
        note: t('admin.shortcuts.within1s'),
      },
      { keys: ['?'], action: t('admin.shortcuts.openSheet') },
    ],
  },
  {
    scope: t('admin.shortcuts.scope.list'),
    rows: [
      { keys: ['j'], action: t('admin.shortcuts.down'), note: t('admin.shortcuts.stopsAtLast') },
      { keys: ['k'], action: t('admin.shortcuts.up'), note: t('admin.shortcuts.stopsAtFirst') },
      { keys: ['↓', '↑'], action: t('admin.shortcuts.sameAsJk') },
      { keys: ['Enter'], action: t('admin.shortcuts.openRow') },
    ],
  },
  {
    scope: t('admin.shortcuts.scope.listAndDetail'),
    rows: [
      { keys: ['1'], action: t('admin.shortcuts.setStatus', { status: statusMeta('in_progress').label }) },
      { keys: ['2'], action: t('admin.shortcuts.setStatus', { status: statusMeta('resolved').label }) },
      {
        keys: ['3'],
        action: t('admin.shortcuts.setStatus', { status: statusMeta('deployed').label }),
        note: t('admin.shortcuts.noUndo'),
      },
      { keys: ['H'], action: t('admin.shortcuts.keep') },
      { keys: ['U'], action: t('admin.shortcuts.undo'), note: t('admin.shortcuts.shownFor5s') },
      { keys: ['A'], action: t('admin.shortcuts.assign') },
      { keys: ['M'], action: t('admin.shortcuts.markRead') },
    ],
  },
  {
    scope: t('admin.shortcuts.scope.detail'),
    rows: [
      { keys: ['Esc'], action: t('admin.shortcuts.back'), note: t('admin.shortcuts.oneLevel') },
      { keys: ['⌘↵', 'Ctrl+↵'], action: t('admin.shortcuts.comment') },
    ],
  },
])
</script>

<template>
  <ShortcutSheet
    :model-value="modelValue"
    :title="t('admin.shortcuts.title')"
    :groups="groups"
    @update:model-value="emit('update:modelValue', $event)"
  />
</template>
