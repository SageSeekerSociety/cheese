<script setup lang="ts">
/**
 * ShortcutSheet.vue — `?` 打开的那张快捷键表，主应用（AppShortcutSheet）和后台
 * （AdminShortcutSheet）共用这一个长相。表按**作用域分组**：一个键现在管不管用取决于
 * 焦点在哪，分组标题说的就是这件事。
 */
import type { ShortcutGroup } from './shortcutSheet'

import { nextTick, watch } from 'vue'

import { DIALOG_WIDTH } from '@/components/base/dialogSize'
import { t } from '@/i18n'

const props = defineProps<{ modelValue: boolean; title: string; groups: ShortcutGroup[] }>()

const emit = defineEmits<{ (e: 'update:modelValue', v: boolean): void }>()

// 这张表是 `?` 和输入框旁的按钮经一个开关打开的，不是从对话框自己的 activator：关上时
// VDialog 不知道该把焦点还给谁，就落到 <body>。打开时记下焦点在哪，关上还回去 —— 等
// 一拍再还：同一拍里 VDialog 圈焦点的监听还没撤，会把焦点又抢回对话框（它自己还给
// activator 时也是这样等的）。
let opener: HTMLElement | null = null
watch(
  () => props.modelValue,
  async (open) => {
    if (open) {
      opener = document.activeElement instanceof HTMLElement ? document.activeElement : null
      return
    }
    const back = opener
    opener = null
    await nextTick()
    if (back?.isConnected) back.focus({ preventScroll: true })
  }
)
</script>

<template>
  <!-- Closing reuses VDialog's own Esc and click-outside; no extra button. The named transition
       gives the 0.3s panel motion (Vuetify's default is just over 0.2s). -->
  <v-dialog
    :model-value="props.modelValue"
    :max-width="DIALOG_WIDTH.md"
    transition="sheet-fade"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <v-card class="ssheet" rounded="lg" flat>
      <h2 class="ssheet__title">{{ title }}</h2>
      <!-- 能聚焦：表长了要靠方向键滚，而且对话框里得有一个能拿到焦点的地方，VDialog 才
           把 Tab 圈在里面；否则 Tab 一下就跑到被遮住的页面上，Esc 也就关不掉它了。 -->
      <div class="ssheet__body" tabindex="0" :aria-label="title">
        <section v-for="group in groups" :key="group.scope" class="ssheet__group">
          <h3 class="ssheet__scope t-eyebrow-read">{{ group.scope }}</h3>
          <div class="ssheet__rows">
            <template v-for="row in group.rows" :key="row.keys.join('+')">
              <span class="ssheet__keys">
                <template v-for="(key, index) in row.keys" :key="key">
                  <span v-if="index > 0" class="ssheet__joiner">{{
                    row.sequence ? t('global.shortcuts.then') : '/'
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

<!-- Deliberately unscoped: the dialog content is rendered by VOverlay through a teleport and never
     carries this component's scope id. The `sheet-fade` class is used only here. -->
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
