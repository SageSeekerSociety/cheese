<script setup lang="ts">
/**
 * 布局层（`components/layout-area`）在预览站里的试验台：六个和业务无关的面板，默认摆法是
 * 「左边一条导航、中间对话、右边上下两格」，「笔记」不在任何格里。拖页签、分屏、收起、放大、
 * 关掉再从「+」拿回来、刷新页面、把窗口拉窄，都在这里试；面板里的输入框和滚动位置用来看
 * 实例有没有被重建。
 *
 * `titled` 换一套摆法：右边那一格里「文件」「预览」各带自己的标题栏，一次显示一格，没有页签；
 * 上面那一排按钮把它们翻到前面，标题栏上的 ✕ 收起那一格（试验台替页面做这件事）。「预览」的
 * 标题栏前面多一颗「返回」，回到「文件」：看的是 `head` 插槽。
 */
import type { LayoutArrangement, LayoutPanel } from '@/components/layout-area'

import { computed, ref } from 'vue'

import CatalogLayoutSample from './CatalogLayoutSample.vue'

import BaseButton from '@/components/base/BaseButton.vue'
import { LayoutArea } from '@/components/layout-area'

const props = withDefaults(
  defineProps<{ storageKey: string; locked?: boolean; narrow?: boolean; titled?: boolean }>(),
  { locked: false, narrow: undefined, titled: false }
)

const sample = (label: string, rows?: number) => ({ component: CatalogLayoutSample, props: { label, rows } })

const tabbed: LayoutPanel[] = [
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

const tabbedArrangement: LayoutArrangement = {
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

// 带标题栏的一套：右边那一格收着时，两块都藏起来。
const placeHidden = ref(false)
const titledPanels = computed<LayoutPanel[]>(() => [
  tabbed[0],
  tabbed[1],
  { id: 'files', title: '文件', icon: 'mdi-file-outline', ...sample('文件'), titled: true, hidden: placeHidden.value },
  {
    id: 'preview',
    title: '预览',
    icon: 'mdi-eye-outline',
    ...sample('预览', 30),
    titled: true,
    hidden: placeHidden.value,
  },
])
const titledArrangement: LayoutArrangement = {
  root: {
    direction: 'row',
    children: [
      { panels: ['nav'], size: 1 },
      { panels: ['chat'], size: 2 },
      { panels: ['files', 'preview'], size: 2 },
    ],
  },
}

const panels = computed(() => (props.titled ? titledPanels.value : tabbed))
const arrangement = computed(() => (props.titled ? titledArrangement : tabbedArrangement))
const titleOf = (id: string) => panels.value.find((panel) => panel.id === id)?.title ?? id

const area = ref<{ reset(): void; open(id: string): void }>()
function show(id: string) {
  placeHidden.value = false
  area.value?.open(id)
}
</script>

<template>
  <div class="layout-demo">
    <div class="layout-demo__bar">
      <template v-if="titled">
        <BaseButton kind="ghost" size="sm" data-demo-show="files" @click="show('files')">文件</BaseButton>
        <BaseButton kind="ghost" size="sm" data-demo-show="preview" @click="show('preview')">预览</BaseButton>
      </template>
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
        @close="placeHidden = true"
      >
        <template v-if="titled" #head="{ id }">
          <BaseButton
            v-if="id === 'preview'"
            kind="ghost"
            size="sm"
            icon="mdi-arrow-left"
            aria-label="返回"
            title="返回"
            @click="show('files')"
          />
          <span class="layout-demo__title">{{ titleOf(id) }}</span>
        </template>
      </LayoutArea>
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
  gap: 4px;
  justify-content: flex-end;
  padding: 4px 8px;
  border-bottom: 1px solid var(--line);
}

.layout-demo__title {
  padding: 0 8px;
  font-size: 13px;
  font-weight: 600;
  color: var(--ink);
}

.layout-demo__area {
  flex: 1 1 auto;
  min-height: 0;
}
</style>
