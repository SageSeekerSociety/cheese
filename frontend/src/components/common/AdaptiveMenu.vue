<script setup lang="ts">
// 一份操作清单，桌面上是一个下拉菜单（v-menu），手机上（< 960px）是底部动作面板
// （MobileActionSheet）。页面只写一次清单，不自己分两支。
//
// 全应用同一时间只留一份菜单：谁开谁登记，右键那一份（弹在鼠标那一点上）开的时候把
// 当时开着的那一份收掉（见 openMenus）。
//
//   <AdaptiveMenu :actions="actions" title="话题">
//     <template #activator="{ props }">
//       <v-btn v-bind="props" icon="mdi-dots-horizontal" :aria-label="…" />
//     </template>
//   </AdaptiveMenu>
//
// 激活器的 props 两端都要 v-bind 上：桌面上它们是 v-menu 的，手机上是打开面板的
// onClick。#header 只在手机面板上画；#desktopHeader 可在桌面菜单项前放一排表情等内容。
import type { MenuAction } from './menuAction'

import { onScopeDispose, watch } from 'vue'
import { useDisplay } from 'vuetify'

import MobileActionSheet from './MobileActionSheet.vue'
import { menuClosed, menuOpened } from './openMenus'

const open = defineModel<boolean>({ default: false })

const props = withDefaults(
  defineProps<{
    actions: MenuAction[]
    /** 手机面板顶上的小标题。 */
    title?: string
    /** 桌面菜单相对激活器的位置。 */
    location?: 'bottom end' | 'bottom start' | 'top end' | 'top start'
    /** 右键打开时鼠标的位置：给了就弹在这一点上，而不是挂在激活器下面。 */
    point?: [number, number] | null
  }>(),
  { title: undefined, location: 'bottom end', point: null }
)

defineSlots<{
  activator: (scope: { props: Record<string, unknown> }) => unknown
  header?: () => unknown
  desktopHeader?: () => unknown
}>()

const { mdAndUp } = useDisplay()

// 开着的时候把自己登记成「此刻开着的那一份」；右键那一份顺带把上一个收掉（见 openMenus）。
// 落在 post 里判：有的调用方同一拍里既开菜单又给 point，早一拍读会把右键那一份读成点开的。
const closeSelf = () => {
  open.value = false
}
watch(
  open,
  (isOpen) => {
    if (isOpen) menuOpened(closeSelf, props.point !== null)
    else menuClosed(closeSelf)
  },
  { flush: 'post' }
)
// 列表重排把这一份拆掉时 open 不会走 false，别把登记留在那里。
onScopeDispose(() => menuClosed(closeSelf))

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
  <v-menu
    v-if="mdAndUp"
    v-model="open"
    :target="props.point ?? undefined"
    :location="props.point ? 'bottom start' : props.location"
    :offset="props.point ? 2 : 8"
  >
    <template #activator="{ props: activator }">
      <slot name="activator" :props="activator" />
    </template>
    <v-list min-width="160" role="menu">
      <slot name="desktopHeader" />
      <v-list-item
        v-for="action in props.actions"
        :key="action.key"
        role="menuitem"
        :to="action.to"
        :prepend-icon="action.icon"
        :disabled="action.disabled || action.loading"
        :class="{ 'adaptive-menu__item--danger': action.danger }"
        @click="chooseOnDesktop(action)"
      >
        <v-list-item-title>{{ action.label }}</v-list-item-title>
        <template v-if="action.badge" #append>
          <span class="adaptive-menu__badge">{{ action.badge }}</span>
        </template>
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
.adaptive-menu__badge {
  margin-inline-start: 12px;
  color: var(--accent);
  font-size: 13px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}
/* 行的长相（图标、字号、悬停）是每个 v-menu 都有的那一套（style.css）；这里只把
   不可撤销的那一行染成 --danger-ink。类名写两遍压过那一套里同特异度的颜色。 */
.adaptive-menu__item--danger :deep(.v-list-item-title.v-list-item-title),
.adaptive-menu__item--danger :deep(.v-list-item__prepend > .v-icon) {
  color: var(--danger-ink);
}
</style>
