<template>
  <v-navigation-drawer
    :id="SIDEBAR_DRAWER_ID"
    v-model="drawerModel"
    :permanent="permanent"
    :temporary="temporary"
    :class="drawerClass"
    :color="color"
    :border="border"
    :width="isDesktop ? width : undefined"
    v-bind="$attrs"
  >
    <!-- 右边缘拖动改宽度。宽度是四处侧栏共用的一个数（useSidebarWidth）。 -->
    <div v-if="isDesktop" class="sidebar-resizer" :title="t('navigation.sidebar.resize')" @mousedown="startResize" />
    <slot></slot>
  </v-navigation-drawer>
</template>

<script setup lang="ts">
import { computed, watch } from 'vue'
import { useDisplay } from 'vuetify'
import { storeToRefs } from 'pinia'

import { useEscapeLayer } from '@/composables/useEscapeStack'
import { focusSidebarToggle, SIDEBAR_DRAWER_ID, useSidebarCollapse } from '@/composables/useSidebarCollapse'
import { useSidebarWidth } from '@/composables/useSidebarWidth'

import { t } from '@/i18n'
import { useNavigationStore } from '@/stores/navigation'

interface Props {
  // 是否在桌面端强制使用临时抽屉模式，默认 false
  forceMobile?: boolean
  // 自定义颜色，默认为 background（主题语义色，随亮/暗主题切换）。
  // 传值时也请用语义色名，不要用 grey-lighten-5 这类 Material 固定灰阶——
  // 它在两个主题下都是同一个 #FAFAFA，深色下会变成白底浅字。
  color?: string
  // 自定义边框，默认为 sm
  border?: string
  // 自定义类名
  customClass?: string
}

const props = withDefaults(defineProps<Props>(), {
  forceMobile: false,
  color: 'background',
  border: 'sm',
  customClass: '',
})

const { mdAndUp } = useDisplay()
const navigationStore = useNavigationStore()
const { isSecondaryDrawerOpen } = storeToRefs(navigationStore)
const { closeSecondaryDrawer } = navigationStore

// 计算是否为桌面端模式
const isDesktop = computed(() => mdAndUp.value && !props.forceMobile)

// 桌面窄档（960–1180）：这一栏是一只可收起的浮层，rail 顶上那颗开关管它。宽档里它
// 常驻，手机上是另一套（`isSecondaryDrawerOpen`）。
const {
  compact: compactSidebar,
  open: sidebarOpen,
  setExpanded: setSidebarExpanded,
  close: closeSidebar,
} = useSidebarCollapse()

const { width, setWidth } = useSidebarWidth()

// 拖右边缘：宽度 = 指针到抽屉左边缘的距离。抽屉左边还有一级导航那一条，所以不能
// 直接拿指针的 x 当宽度。
function startResize(e: MouseEvent) {
  e.preventDefault()
  const left = (e.currentTarget as HTMLElement).parentElement?.getBoundingClientRect().left ?? 0
  const move = (ev: MouseEvent) => setWidth(ev.clientX - left)
  const stop = () => {
    window.removeEventListener('mousemove', move)
    window.removeEventListener('mouseup', stop)
    document.body.style.cursor = ''
    document.body.style.userSelect = ''
  }
  window.addEventListener('mousemove', move)
  window.addEventListener('mouseup', stop)
  document.body.style.cursor = 'col-resize'
  document.body.style.userSelect = 'none'
}

// 计算是否使用 permanent 模式：只有宽档的桌面侧栏才常驻。
const permanent = computed(() => isDesktop.value && !compactSidebar.value)

// 计算是否使用 temporary 模式
const temporary = computed(() => !permanent.value)

// 双向绑定的抽屉模型
const drawerModel = computed({
  get: () => {
    if (permanent.value) return true
    if (isDesktop.value) return sidebarOpen.value
    return isSecondaryDrawerOpen.value
  },
  set: (value: boolean) => {
    if (permanent.value) return
    // 抽屉自己也会改这个 model —— 点遮罩、换路由，都从这儿走。这些是「这一次」的
    // 收起，不是人的选择，所以不写进 localStorage：记住的只该是 rail 上那颗开关
    // 按下的那一下（`toggle()` 才 persist）。
    if (isDesktop.value) setSidebarExpanded(value, false)
    else navigationStore.setSecondaryDrawerOpen(value)
  },
})

// 窄档里这一栏是一只可收起的浮层：它开着的时候，按 Esc 关掉它、焦点回到 rail 上打开
// 它的那颗开关。Vuetify 的抽屉自己不管键盘，而「按 Esc 关掉浮层」这条对键盘和读屏
// 用户是必须的（宽档常驻、手机上另一套，都不在这里处理）。
//
// Esc 走全局的栈（`useEscapeStack`），不是这里自己一颗 window 监听：这一档里话题的
// 工作面板浮层也可能同时开着，一下 Esc 只该打发最上面那层——各关各的会把两层一起收，
// 人分不清刚才关掉的是哪一层。栈顶是谁，由打开顺序定。
function dismissSidebarOverlay() {
  closeSidebar()
  focusSidebarToggle()
}
useEscapeLayer(
  computed(() => isDesktop.value && compactSidebar.value && sidebarOpen.value),
  dismissSidebarOverlay
)

// 计算抽屉的 CSS 类
const drawerClass = computed(() => {
  const classes = ['page-sidebar', 'border-e-0', 'border-b-0']
  if (!isDesktop.value) {
    classes.push('rounded-0')
  }
  if (props.customClass) {
    classes.push(props.customClass)
  }
  return classes.join(' ')
})

// 路由变化时自动关闭移动端抽屉
import { useRouter } from 'vue-router'
const router = useRouter()

watch(
  () => router.currentRoute.value.fullPath,
  () => {
    if (!isDesktop.value) {
      closeSecondaryDrawer()
    } else if (compactSidebar.value && sidebarOpen.value) {
      // 平板横放：从浮层里点进一个房间（或别的层）就把它收起来。收起的是这一次，不是
      // 人的选择——`closeSidebar` 不写 localStorage，别把这一下记成「我平时要它收着」。
      closeSidebar()
    }
  }
)
</script>

<style scoped>
/* 抓手比看得见的那条宽：11px 好抓，中间 2px 在悬停时变成琥珀。 */
.sidebar-resizer {
  position: absolute;
  top: 0;
  right: -6px;
  bottom: 0;
  z-index: var(--z-raised-4);
  width: 11px;
  cursor: col-resize;
}
.sidebar-resizer::after {
  content: '';
  position: absolute;
  top: 0;
  right: 5px;
  bottom: 0;
  width: 2px;
  background: transparent;
  transition: background-color var(--dur-quick) var(--ease-standard);
}
.sidebar-resizer:hover::after {
  background: var(--accent);
}
</style>
