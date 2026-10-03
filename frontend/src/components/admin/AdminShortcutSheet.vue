<script setup lang="ts">
import { computed } from 'vue'

import { DIALOG_WIDTH } from '@/components/base/dialogSize'
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

const props = defineProps<{ modelValue: boolean }>()

const emit = defineEmits<{ (e: 'update:modelValue', v: boolean): void }>()

interface ShortcutRow {
  keys: string[]
  /** 序列键（`G` 然后 `Q`）与二选一（`↓` / `↑`）在表里长得像，读起来是两回事。 */
  sequence?: boolean
  action: string
  note?: string
}

interface ShortcutGroup {
  scope: string
  rows: ShortcutRow[]
}

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
  <!-- 关闭走两处既有行为，不另做按钮：VDialog 自己吃 `Esc`，点浮层外面也关。
       `transition` 换成命名过渡是为了 §7.7 的「面板 0.3s」——Vuetify 默认那支
       只有 0.2s 出头。 -->
  <v-dialog
    :model-value="props.modelValue"
    :max-width="DIALOG_WIDTH.md"
    transition="sheet-fade"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <v-card class="ssheet" rounded="lg" flat>
      <h2 class="ssheet__title">{{ t('admin.shortcuts.title') }}</h2>
      <div class="ssheet__body">
        <section v-for="group in groups" :key="group.scope" class="ssheet__group">
          <h3 class="ssheet__scope t-eyebrow-read">{{ group.scope }}</h3>
          <div class="ssheet__rows">
            <template v-for="row in group.rows" :key="row.keys.join('+')">
              <span class="ssheet__keys">
                <template v-for="(key, index) in row.keys" :key="key">
                  <span v-if="index > 0" class="ssheet__joiner">{{
                    row.sequence ? t('admin.shortcuts.then') : '/'
                  }}</span>
                  <kbd class="ssheet__kbd">{{ key }}</kbd>
                </template>
              </span>
              <span class="ssheet__action">{{ row.action }}</span>
              <span class="ssheet__note">{{ row.note }}</span>
            </template>
          </div>
        </section>
      </div>
    </v-card>
  </v-dialog>
</template>

<style scoped>
.ssheet {
  padding: 16px;
  background: var(--surface);
  box-shadow: var(--shadow-2);
}

.ssheet__title {
  margin: 0 0 12px;
  font-size: 15px;
  font-weight: 600;
  line-height: var(--lh-15);
  color: var(--ink);
}

/* 19 行 + 4 个分组标题在 767px 高的本子上放不下，让它在这里滚，别去挤对话框的
   外边距（挤了就会贴着屏幕边）。 */
.ssheet__body {
  max-height: calc(100vh - 96px);
  overflow-y: auto;
}

.ssheet__group + .ssheet__group {
  margin-top: 16px;
}

.ssheet__scope {
  margin: 0 0 4px;
}

.ssheet__rows {
  display: grid;
  grid-template-columns: max-content minmax(0, 1fr) max-content;
  align-items: center;
  gap: 4px 12px;
}

.ssheet__keys {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  white-space: nowrap;
}

.ssheet__joiner {
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--muted);
}

/* 键帽：等宽字体 + 一层浅底，读起来才像「一个可以按的东西」而不是正文里的字母。
   `min-width` 管住单字符键的宽度，`Ctrl+↵` 这种自己撑开。 */
.ssheet__kbd {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  box-sizing: border-box;
  height: 20px;
  min-width: 20px;
  padding: 0 4px;
  font-family: var(--font-mono);
  font-size: 12px;
  font-weight: 600;
  line-height: var(--lh-12);
  color: var(--ink);
  background: var(--fill-2);
  border-top-left-radius: var(--radius-sm);
  border-top-right-radius: var(--radius-sm);
  border-bottom-right-radius: var(--radius-sm);
  border-bottom-left-radius: var(--radius-sm);
}

.ssheet__action {
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--text);
}

.ssheet__note {
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--muted);
  white-space: nowrap;
}
</style>

<!-- 这一块故意不加 scoped：对话框的内容节点由 VOverlay 渲染、还隔着 teleport，
     带不上本组件的 scope id，加 scoped 就等于这段过渡永远不生效。类名 `sheet-fade`
     全仓只有这里用，没有外溢。 -->
<style>
.sheet-fade-enter-active,
.sheet-fade-leave-active {
  transition:
    opacity 0.3s ease,
    transform 0.3s ease;
}

.sheet-fade-enter-from,
.sheet-fade-leave-to {
  opacity: 0;
  transform: translateY(8px);
}
</style>
