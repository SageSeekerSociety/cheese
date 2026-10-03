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
          <div class="so__content">
            <slot />
          </div>
        </main>
        <button type="button" class="so__close" :aria-label="closeLabel" :title="closeTitle" @click="emit('close')">
          <v-icon icon="mdi-close" size="20" />
        </button>
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
          <slot v-else />
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

/* 桌面：左边目录贴着分界线靠右，右边内容靠左，两边中间就是视线落的地方。 */
.so__side {
  display: flex;
  flex: 1 1 232px;
  justify-content: flex-end;
  min-width: 232px;
  max-width: 440px;
  padding: 48px 16px 24px;
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
}

.so__content {
  max-width: 820px;
  padding: 24px 72px 48px 16px;
}

.so__close {
  position: absolute;
  top: 48px;
  right: 40px;
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
</style>
