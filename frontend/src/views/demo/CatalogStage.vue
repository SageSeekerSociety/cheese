<script setup lang="ts">
// 单组件页里的一格舞台：按工具条上选的设备档画这一格。
//
//   「跟随容器」        → 就地渲染，宽度就是这一栏的宽度（这一页被嵌进文档的 iframe
//                        里看的时候，这也就是它真正的宽度）
//   桌面 / 平板 / 手机  → 真 px 的 iframe，外面等比缩（CatalogFrame.vue）
//   双端               → 上面两头的组合：桌面那台和手机那台并排
import type { CatalogEntry, CatalogState } from './catalog'

import { computed } from 'vue'

import { stateProps } from './catalog'
import CatalogFrame from './CatalogFrame.vue'
import { deviceOf, DUAL_DEVICES, stageHref, useStudio } from './demoCatalogStudio'

const props = defineProps<{
  entry: CatalogEntry
  state: CatalogState
  index: number
  /** 这一格重放了几次：换掉 key，把这一格重新挂一遍，动效从头走。 */
  replay: number
}>()

const studio = useStudio()

const frames = computed(() => {
  if (studio.dual) return DUAL_DEVICES.map(deviceOf)
  const device = deviceOf(studio.device)
  return device.width === null ? [] : [device]
})

const href = computed(() =>
  stageHref(props.entry.id, props.index, { theme: studio.theme, motion: studio.motion, bg: studio.bg })
)

/** 舞台底下那一层：产品里这两种底色的地方都有（页面是 canvas，面板里是 surface）。 */
</script>

<template>
  <div class="stage" :data-bg="studio.bg">
    <div v-if="frames.length" class="stage-frames">
      <CatalogFrame
        v-for="device in frames"
        :key="`${device.id}-${replay}`"
        :href="href"
        :width="device.width ?? 0"
        :label="device.label"
        :title="`${entry.title} · ${state.name}（${device.label}）`"
      />
    </div>
    <!-- 就地渲染那一档：key 跟着重放走，重挂一遍就是从头再动一次。 -->
    <div v-else :key="replay" class="stage-live">
      <v-layout v-if="entry.layout">
        <component :is="entry.component" v-bind="stateProps(entry, state)">{{ state.slot }}</component>
      </v-layout>
      <component :is="entry.component" v-else v-bind="stateProps(entry, state)">{{ state.slot }}</component>
    </div>
  </div>
</template>

<style scoped>
/* 摆得下这一格的地方。给一块底、一条边，免得组件自己没边的时候看不出它有多宽
   （KPI 卡、页签条都是「自己不带框」的那种）。 */
.stage {
  padding: 12px;
  background: var(--surface);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-lg);
}

.stage[data-bg='canvas'] {
  background: var(--canvas);
}

.stage-frames {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  align-items: flex-start;
}
</style>
