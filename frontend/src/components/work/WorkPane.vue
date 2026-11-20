<script setup lang="ts">
// 工作面板的一格内容。面板自己排页签时（手机、平板浮层、频道里打开的任务）它就在面板的内容区
// 里，选中才显示；桌面上每一格是外壳布局区里的一块（layouts/pagePanels.ts），`target` 是
// 那一块的落点，它整个传送过去，显示与否由那一格自己定。同一个实例，换摆法、换档都不重挂。
defineProps<{
  /** 布局区给这一格的落点；不给就留在面板里。 */
  target?: HTMLElement
  /** 留在面板里时，这一格是不是选中的那一格。 */
  shown: boolean
  /** 手机上换页签时进场那一下的类（`WorkPanel` 的 enterClass）。 */
  paneClass?: string
  /** 这一块的元素 id（`workPaneId`）。 */
  paneId?: string
}>()

defineSlots<{
  default?: () => unknown
  /** 贴在这一格底部的那一条（对话看不见时的采纳条）。 */
  dock?: () => unknown
}>()
</script>

<template>
  <Teleport :to="target" :disabled="!target">
    <div
      v-show="!!target || shown"
      :id="paneId"
      class="work-pane"
      :class="[{ 'work-pane--cell': !!target }, paneClass]"
    >
      <div class="work-pane__body"><slot /></div>
      <slot name="dock" />
    </div>
  </Teleport>
</template>

<style scoped>
.work-pane {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  width: 100%;
  min-width: 0;
  min-height: 0;
}

.work-pane--cell {
  height: 100%;
  background: var(--surface);
}

.work-pane__body {
  display: flex;
  flex: 1 1 auto;
  min-width: 0;
  min-height: 0;
}

/* 每一格都占满：按内容收缩的一格会让 Monaco 量到窄宽度后定死，右边空一条。 */
.work-pane__body > :deep(*) {
  flex: 1 1 auto;
  min-width: 0;
}
</style>
