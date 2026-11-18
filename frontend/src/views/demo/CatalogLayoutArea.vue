<script setup lang="ts">
/**
 * 布局层（`components/layout-area`）在预览站里的试验台：六个和业务无关的面板，默认摆法是
 * 「左边一条导航、中间对话、右边上下两格」，「笔记」不在任何格里。拖页签、分屏、收起、放大、
 * 关掉再从「+」拿回来、刷新页面、把窗口拉窄，都在这里试；面板里的输入框和滚动位置用来看
 * 实例有没有被重建。
 *
 * 还没有产品页面用这一层，这里是它唯一的使用者。
 */
import type { LayoutArrangement, LayoutPanel } from '@/components/layout-area'

import { ref } from 'vue'

import CatalogLayoutSample from './CatalogLayoutSample.vue'

import BaseButton from '@/components/base/BaseButton.vue'
import { LayoutArea } from '@/components/layout-area'

const props = withDefaults(defineProps<{ storageKey: string; locked?: boolean; narrow?: boolean }>(), {
  locked: false,
  narrow: undefined,
})

const sample = (label: string, rows?: number) => ({ component: CatalogLayoutSample, props: { label, rows } })

const panels: LayoutPanel[] = [
  { id: 'nav', title: '导航', icon: 'mdi-menu', ...sample('导航', 20), pinned: true, minWidth: 180 },
  { id: 'chat', title: '对话', icon: 'mdi-message-outline', ...sample('对话', 80), minWidth: 320 },
  { id: 'files', title: '文件', icon: 'mdi-file-outline', ...sample('文件'), closable: true },
  {
    id: 'preview',
    title: '预览',
    icon: 'mdi-eye-outline',
    component: CatalogLayoutSample,
    props: { label: '预览', frame: true },
    closable: true,
    alwaysRender: true,
  },
  { id: 'terminal', title: '终端', icon: 'mdi-console', ...sample('终端', 12), closable: true },
  { id: 'notes', title: '笔记', icon: 'mdi-note-outline', ...sample('笔记', 6), closable: true },
]

const arrangement: LayoutArrangement = {
  root: {
    direction: 'row',
    children: [
      { panels: ['nav'], size: 1 },
      { panels: ['chat'], size: 2 },
      {
        direction: 'column',
        size: 2,
        children: [
          { panels: ['files', 'preview'], size: 3 },
          { panels: ['terminal'], size: 1 },
        ],
      },
    ],
  },
}

const area = ref<{ reset(): void }>()
</script>

<template>
  <div class="layout-demo">
    <div class="layout-demo__bar">
      <BaseButton kind="ghost" size="sm" data-layout-reset @click="area?.reset()">恢复默认摆法</BaseButton>
    </div>
    <div class="layout-demo__area">
      <LayoutArea
        ref="area"
        :panels="panels"
        :default-arrangement="arrangement"
        :storage-key="props.storageKey"
        :version="1"
        :locked="props.locked"
        :narrow="props.narrow"
      />
    </div>
  </div>
</template>

<style scoped>
.layout-demo {
  display: flex;
  flex-direction: column;
  height: 600px;
  background: var(--surface);
  border: 1px solid var(--line);
}

.layout-demo__bar {
  display: flex;
  flex: 0 0 auto;
  justify-content: flex-end;
  padding: 4px 8px;
  border-bottom: 1px solid var(--line);
}

.layout-demo__area {
  flex: 1 1 auto;
  min-height: 0;
}
</style>
