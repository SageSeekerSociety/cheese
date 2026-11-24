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
//
// 一项带 children 的是下一层：桌面上在它旁边弹出子菜单（只有一层），手机面板里点它
// 换成那一层（MobileActionSheet）。
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
      <template v-for="action in props.actions" :key="action.key">
        <v-divider v-if="action.divider" class="adaptive-menu__divider" />
        <div v-if="action.section" class="adaptive-menu__section">{{ action.section }}</div>
        <!-- One level of submenu: it opens beside the item on hover, and on click or arrow key. The click
             stops here so the outer menu, which closes on a click inside it, stays open. -->
        <v-menu
          v-if="action.children"
          submenu
          open-on-hover
          open-on-click
          :open-delay="0"
          location="end top"
          :offset="4"
        >
          <template #activator="{ props: sub }">
            <v-list-item
              v-bind="sub"
              role="menuitem"
              aria-haspopup="menu"
              :prepend-icon="action.icon"
              :disabled="action.disabled || action.loading"
              @click.stop
            >
              <v-list-item-title>{{ action.label }}</v-list-item-title>
              <template #append>
                <span v-if="action.value" class="adaptive-menu__value">{{ action.value }}</span>
                <v-icon size="16" class="adaptive-menu__chevron" icon="mdi-chevron-right" />
              </template>
            </v-list-item>
          </template>
          <v-list min-width="240" max-width="340" role="menu" :aria-label="action.label">
            <template v-for="child in action.children" :key="child.key">
              <v-divider v-if="child.divider" class="adaptive-menu__divider" />
              <div v-if="child.section" class="adaptive-menu__section">{{ child.section }}</div>
              <v-list-item
                :role="child.checked === undefined ? 'menuitem' : 'menuitemradio'"
                :aria-checked="child.checked === undefined ? undefined : child.checked ? 'true' : 'false'"
                :to="child.to"
                :disabled="child.disabled || child.loading"
                :class="{ 'adaptive-menu__item--danger': child.danger }"
                @click="chooseOnDesktop(child)"
              >
                <template v-if="child.checked !== undefined || child.icon" #prepend>
                  <v-icon v-if="child.checked" size="16" class="adaptive-menu__check" icon="mdi-check" />
                  <span v-else-if="child.checked === false" class="adaptive-menu__check" aria-hidden="true" />
                  <v-icon v-else :icon="child.icon" />
                </template>
                <v-list-item-title>{{ child.label }}</v-list-item-title>
                <v-list-item-subtitle v-if="child.hint" class="adaptive-menu__hint">{{
                  child.hint
                }}</v-list-item-subtitle>
              </v-list-item>
            </template>
          </v-list>
        </v-menu>
        <v-list-item
          v-else
          :role="action.checked === undefined ? 'menuitem' : 'menuitemradio'"
          :aria-checked="action.checked === undefined ? undefined : action.checked ? 'true' : 'false'"
          :to="action.to"
          :prepend-icon="action.checked === undefined ? action.icon : undefined"
          :disabled="action.disabled || action.loading"
          :class="{ 'adaptive-menu__item--danger': action.danger }"
          @click="chooseOnDesktop(action)"
        >
          <template v-if="action.checked !== undefined" #prepend>
            <v-icon v-if="action.checked" size="16" class="adaptive-menu__check" icon="mdi-check" />
            <span v-else class="adaptive-menu__check" aria-hidden="true" />
          </template>
          <v-list-item-title>{{ action.label }}</v-list-item-title>
          <v-list-item-subtitle v-if="action.hint" class="adaptive-menu__hint">{{ action.hint }}</v-list-item-subtitle>
          <template v-if="action.badge || action.value" #append>
            <span v-if="action.value" class="adaptive-menu__value">{{ action.value }}</span>
            <span v-if="action.badge" class="adaptive-menu__badge">{{ action.badge }}</span>
          </template>
        </v-list-item>
      </template>
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
/* 分隔线：上面几件和「不可撤销的那一件」之间那一条。颜色用 v-divider 自己的那一套，
   这里只给它上下留一点空。 */
.adaptive-menu__divider {
  margin: 4px 0;
}
/* 一组的小标题（「静音一段时间」）：和分隔线一起把下面几项归成一组。 */
.adaptive-menu__section {
  padding: 4px 16px 2px;
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
}
/* 行尾此刻的值：比名字弱一档。 */
.adaptive-menu__value {
  margin-inline-start: 16px;
  color: var(--muted);
  font-size: 13px;
  white-space: nowrap;
}
.adaptive-menu__chevron {
  margin-inline-start: 4px;
  color: var(--muted);
}
.adaptive-menu__check {
  display: inline-block;
  width: 16px;
  margin-inline-end: 12px;
  color: var(--ink);
}
.adaptive-menu__hint {
  white-space: normal;
}
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
