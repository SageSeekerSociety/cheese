<script setup lang="ts">
// 设置的外框：盖在整个窗口上的一层，个人设置、空间设置、项目设置三处共用。
//
// 桌面上左边灰底是目录（上面写这是谁的设置），右边白底是这一页，右上角一颗 × 关掉。
// 手机上是整屏：先是目录，点进一项是那一页，左上角返回目录；在目录上返回就是关掉。
//
// 这一层不是弹窗：它有自己的地址，每一项是一条路由，刷新、分享链接都照旧。关掉去
// 哪由用这个外框的那一级决定（lib/settingsReturn），这里只发 `close`。
//
// Esc 关掉，但页面里开着的菜单或对话框先吃掉这一下：确认框上按 Esc 是取消确认，不
// 是连设置一起关。
import type { NavTarget } from '@/lib/navTarget'

import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useDisplay } from 'vuetify'

import { useFocusReturn } from '@/composables/useFocusReturn'

import NavLink from '@/components/common/NavLink.vue'

export interface SettingsItem {
  key: string
  label: string
  to: NavTarget
  icon?: string
  /** 不可撤回的那一项（归档项目）：红字，排在最后。 */
  danger?: boolean
}

export interface SettingsGroup {
  key: string
  title?: string
  items: SettingsItem[]
}

const props = defineProps<{
  /** 这一层是什么（「个人设置」），读屏读它，手机顶栏在目录上写它。 */
  label: string
  groups: SettingsGroup[]
  /** 当前那一项的 key；手机上停在目录时为空。 */
  active: string | null
  /** 手机上从某一页回目录去哪。 */
  indexTo: NavTarget
  closeLabel: string
  /** 关闭按钮悬停时的说明，带上 Esc：键盘提示只放这里，不画在屏幕上。 */
  closeTitle: string
  backLabel: string
}>()

const emit = defineEmits<{ close: [] }>()

defineSlots<{
  /** 目录上方：这是谁的设置。 */
  head?: () => unknown
  default?: () => unknown
}>()

const { mdAndUp } = useDisplay()

const activeItem = computed(() => {
  for (const group of props.groups) {
    const item = group.items.find((i) => i.key === props.active)
    if (item) return item
  }
  return null
})

/** 手机上没选中哪一项就是停在目录。 */
const showIndex = computed(() => !mdAndUp.value && !activeItem.value)

const layer = ref<HTMLElement | null>(null)

function onKeydown(event: KeyboardEvent) {
  if (event.key !== 'Escape' || event.defaultPrevented) return
  if (document.querySelector('.v-overlay--active')) return
  emit('close')
}

// 盖住的那一层（整个应用）读屏读不到、Tab 也走不进去；焦点落进这一层。
let appRoot: HTMLElement | null = null
onMounted(() => {
  window.addEventListener('keydown', onKeydown)
  appRoot = document.getElementById('app')
  appRoot?.setAttribute('inert', '')
  layer.value?.focus()
})
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeydown)
  appRoot?.removeAttribute('inert')
})

// 关掉（这一层卸载）时把焦点还回打开设置的那一处。放在最后注册：它的 onBeforeUnmount
// 要在上面摘掉 inert 之后才跑，焦点才落得回被盖住的那一层里。
useFocusReturn(ref(true))
</script>

<template>
  <Teleport to="body">
    <div ref="layer" class="so" role="dialog" aria-modal="true" :aria-label="label" tabindex="-1">
      <template v-if="mdAndUp">
        <div class="so__side">
          <nav class="so__nav" :aria-label="label">
            <slot name="head" />
            <div v-for="group in groups" :key="group.key" class="so__group">
              <div v-if="group.title" class="so__group-title">{{ group.title }}</div>
              <NavLink
                v-for="item in group.items"
                :key="item.key"
                :to="item.to"
                replace
                class="so__item"
                :class="{ 'so__item--active': item.key === active, 'so__item--danger': item.danger }"
                :aria-current="item.key === active ? 'page' : undefined"
              >
                <v-icon v-if="item.icon" :icon="item.icon" size="16" class="so__icon" />
                {{ item.label }}
              </NavLink>
            </div>
          </nav>
        </div>
        <main class="so__main">
          <div class="so__close-layer">
            <button type="button" class="so__close" :aria-label="closeLabel" :title="closeTitle" @click="emit('close')">
              <v-icon icon="mdi-close" size="20" />
            </button>
          </div>
          <div class="so__content">
            <slot />
          </div>
        </main>
      </template>

      <template v-else>
        <header class="so__bar">
          <button v-if="showIndex" type="button" class="so__back" :aria-label="closeLabel" @click="emit('close')">
            <v-icon icon="mdi-chevron-left" size="24" />
          </button>
          <NavLink v-else :to="indexTo" replace class="so__back" :aria-label="backLabel">
            <v-icon icon="mdi-chevron-left" size="24" />
          </NavLink>
          <span class="so__bar-title">{{ showIndex ? label : activeItem?.label }}</span>
        </header>
        <div class="so__phone">
          <nav v-if="showIndex" class="so__index" :aria-label="label">
            <slot name="head" />
            <div v-for="group in groups" :key="group.key" class="so__group">
              <div v-if="group.title" class="so__group-title">{{ group.title }}</div>
              <div class="so__index-card">
                <NavLink
                  v-for="item in group.items"
                  :key="item.key"
                  :to="item.to"
                  replace
                  class="so__index-row"
                  :class="{ 'so__item--danger': item.danger }"
                >
                  <span>{{ item.label }}</span>
                  <v-icon icon="mdi-chevron-right" size="18" class="so__chevron" />
                </NavLink>
              </div>
            </div>
          </nav>
          <div v-else class="so__content">
            <slot />
          </div>
        </div>
      </template>
    </div>
  </Teleport>
</template>

<style scoped>
.so {
  position: fixed;
  inset: 0;
  /* One below Vuetify's overlays (2000), not equal: menus and dialogs opened in
     here live in body > .v-overlay-container, which page-load tooltips create
     before this layer mounts, so on a tie this layer paints over every one of
     them and a dropdown opens invisible. */
  z-index: 1999;
  display: flex;
  background: var(--surface);
  animation: so-in var(--dur-base) var(--ease-out);
}

.so:focus {
  outline: none;
}

@keyframes so-in {
  from {
    opacity: 0;
  }
}

@media (prefers-reduced-motion: reduce) {
  .so {
    animation: none;
  }
}

/* 桌面：「目录 + 内容列」作为一组在窗口里居中。灰栏从窗口左缘铺到分界线，目录（216）
   贴着分界线靠右；内容列（border-box 720，内距已含在内）贴着分界线靠左。灰栏宽取
   max(264, (窗口 − 720) / 2)：窗口够宽时左边灰栏和右边留白一样宽，这一组正好居中；
   窄到 1248 以下就守住 264（目录 216 + 两侧各 24）。
   以前灰栏封顶 440、内容列再贴左，1920 宽时右边空出 660px；第一版改成灰栏定宽 264、
   内容列在剩下的地方居中，又让目录和内容之间隔出 500 多 px，两边看着不是一页。 */
.so__side {
  display: flex;
  flex: 0 0 auto;
  justify-content: flex-end;
  width: max(264px, calc((100% - 720px) / 2));
  padding: 48px 24px 24px;
  overflow-y: auto;
  border-right: 1px solid var(--line);
  background: var(--canvas);
}

.so__nav {
  display: flex;
  flex-direction: column;
  gap: 20px;
  width: 216px;
}

.so__group {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.so__group-title {
  padding: 0 10px 6px;
  color: var(--faint);
  font-size: 12px;
  font-weight: 600;
  line-height: var(--lh-12);
}

.so__item {
  display: flex;
  gap: 8px;
  align-items: center;
  min-height: 36px;
  padding: 0 10px;
  border-radius: var(--radius-md);
  color: var(--text);
  font-size: 14px;
  line-height: var(--lh-14);
  text-decoration: none;
  transition: background-color var(--dur-quick) var(--ease-standard);
}

.so__item:hover {
  background: var(--fill-2);
}

.so__item:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: -2px;
}

.so__item--active,
.so__item--active:hover {
  background: var(--line-2);
  color: var(--ink);
  font-weight: 600;
}

.so__item--danger {
  color: var(--danger-ink);
}

.so__icon {
  color: var(--faint);
}

.so__main {
  flex: 1 1 800px;
  min-width: 0;
  overflow-y: auto;
  /* 有滚动条时内容列仍居中：槽位常驻，不随滚动条出现/消失而左右跳一格，和项目页一致。 */
  scrollbar-gutter: stable;
}

/* 关闭按钮单独一层：它得跟着内容列右缘走，又不能随内容滚走。这一层粘在滚动口顶上
   （sticky，高 0 不占地方），和 `.so__content` 同宽同位置，所以按钮右缘始终贴着内容
   列右缘，往下滚一屏也钉在原处。pointer-events 关掉，只让按钮自己收点击，别的一层
   空着的地方点击照旧落到底下。 */
.so__close-layer {
  position: sticky;
  top: 0;
  z-index: 1;
  height: 0;
  max-width: 720px;
  pointer-events: none;
}

/* 设置各页共用的一条内容列：最宽 720、贴着分界线、四边内距统一 24，正好容下一行设置
   （672 卡片宽，见 settings-card.css）。各页自己不再设宽度和水平内距，都交给这一条。
   居中由灰栏的宽度负责（见 .so__side）。 */
.so__content {
  max-width: 720px;
  padding: 24px;
}

.so__close {
  /* 落在内容列上方的内距里（页头从 48 开始：内容列 24 + 页面 24），右缘对齐内容列
     右缘。放在 48 那一条会压住页头右边的主操作（我的设备页的「添加设备」）。 */
  position: absolute;
  top: 6px;
  right: 0;
  pointer-events: auto;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  border: 1px solid var(--line-2);
  border-radius: var(--radius-pill);
  background: var(--surface);
  color: var(--muted);
  cursor: pointer;
  transition:
    background-color var(--dur-quick) var(--ease-standard),
    color var(--dur-quick) var(--ease-standard);
}

.so__close:hover {
  background: var(--fill);
  color: var(--ink);
}

.so__close:focus-visible {
  outline: 2px solid var(--focus-ring);
  outline-offset: 2px;
}

/* 手机：整屏，一条顶栏加下面会滚的一块。 */
.so__bar {
  position: absolute;
  top: 0;
  right: 0;
  left: 0;
  display: flex;
  gap: 4px;
  align-items: center;
  height: 52px;
  padding: 0 8px;
  border-bottom: 1px solid var(--line);
  background: var(--canvas);
}

.so__back {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 44px;
  height: 44px;
  border: 0;
  border-radius: var(--radius-md);
  background: none;
  color: var(--ink);
  text-decoration: none;
  cursor: pointer;
}

.so__bar-title {
  color: var(--ink);
  font-size: 15px;
  font-weight: 600;
  line-height: var(--lh-15);
}

.so__phone {
  position: absolute;
  inset: 52px 0 0;
  overflow-y: auto;
}

.so__index {
  display: flex;
  flex-direction: column;
  gap: 20px;
  min-height: 100%;
  padding: 16px;
  box-sizing: border-box;
  background: var(--canvas);
}

.so__index .so__group-title {
  padding: 0 4px 6px;
}

.so__index-card {
  overflow: hidden;
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  background: var(--surface);
}

.so__index-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 48px;
  padding: 0 12px 0 16px;
  color: var(--ink);
  font-size: 15px;
  line-height: var(--lh-15);
  text-decoration: none;
}

.so__index-row + .so__index-row {
  border-top: 1px solid var(--line);
}

.so__chevron {
  color: var(--faint);
}

/* 手机外壳（窄于 960，和 mdAndUp 同一条线）：进到某一页时内容列照旧最宽 720 居中，
   水平内距由这一层给，页面自己只留竖向的。平板 768–959 因此不再贴着左边。 */
@media (max-width: 959.98px) {
  .so__content {
    margin-inline: auto;
    padding: 0 16px;
  }
}

/* 触屏上手指点得中（docs/design-system.md §4、§10.1）：目录项从 36px 提到 44px，
   关闭按钮撑到 44×44。 */
@media (pointer: coarse) {
  .so__item {
    min-height: 44px;
  }

  .so__close {
    width: 44px;
    height: 44px;
  }
}
</style>
