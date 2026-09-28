<script setup lang="ts">
// 一份操作清单，桌面上是一个下拉菜单（v-menu），手机上（< 960px）是底部动作面板
// （MobileActionSheet）。页面只写一次清单，不自己分两支。
//
//   <AdaptiveMenu :actions="actions" title="话题">
//     <template #activator="{ props }">
//       <v-btn v-bind="props" icon="mdi-dots-horizontal" :aria-label="…" />
//     </template>
//   </AdaptiveMenu>
//
// 激活器的 props 两端都要 v-bind 上：桌面上它们是 v-menu 的，手机上是打开面板的
// onClick。#header 只在手机面板上画（一排表情、一行标题）。
import type { MenuAction } from './menuAction'

import { useDisplay } from 'vuetify'

import MobileActionSheet from './MobileActionSheet.vue'

const open = defineModel<boolean>({ default: false })

const props = withDefaults(
  defineProps<{
    actions: MenuAction[]
    /** 手机面板顶上的小标题。 */
    title?: string
    /** 桌面菜单相对激活器的位置。 */
    location?: 'bottom end' | 'bottom start' | 'top end' | 'top start'
  }>(),
  { title: undefined, location: 'bottom end' }
)

defineSlots<{
  activator: (scope: { props: Record<string, unknown> }) => unknown
  header?: () => unknown
}>()

const { mdAndUp } = useDisplay()

// 桌面菜单里带 `to` 的那一行自己就是链接（v-list-item :to），这里只跑 onSelect。
function chooseOnDesktop(action: MenuAction) {
  if (action.disabled || action.loading) return
  action.onSelect?.()
}

const sheetActivator = () => ({
  onClick: () => {
    open.value = true
  },
  'aria-haspopup': 'dialog',
  'aria-expanded': open.value ? 'true' : 'false',
})
</script>

<template>
  <v-menu v-if="mdAndUp" v-model="open" :location="props.location" :offset="8">
    <template #activator="{ props: activator }">
      <slot name="activator" :props="activator" />
    </template>
    <v-list class="menu-list" nav density="compact" min-width="160">
      <v-list-item
        v-for="action in props.actions"
        :key="action.key"
        :to="action.to"
        :disabled="action.disabled || action.loading"
        :class="{ 'adaptive-menu__item--danger': action.danger }"
        @click="chooseOnDesktop(action)"
      >
        <template #prepend>
          <v-icon size="18" class="adaptive-menu__icon" aria-hidden="true">{{ action.icon }}</v-icon>
        </template>
        <v-list-item-title>{{ action.label }}</v-list-item-title>
      </v-list-item>
    </v-list>
  </v-menu>
  <template v-else>
    <slot name="activator" :props="sheetActivator()" />
    <MobileActionSheet v-model="open" :actions="props.actions" :title="props.title">
      <template v-if="$slots.header" #header><slot name="header" /></template>
    </MobileActionSheet>
  </template>
</template>

<style scoped>
.adaptive-menu__icon {
  margin-inline-end: 12px;
  color: var(--muted);
}
.adaptive-menu__item--danger :deep(.v-list-item-title),
.adaptive-menu__item--danger .adaptive-menu__icon {
  color: var(--danger-ink);
}
</style>
