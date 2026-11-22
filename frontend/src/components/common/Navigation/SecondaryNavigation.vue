<template>
  <v-navigation-drawer
    :id="SIDEBAR_DRAWER_ID"
    v-model="drawerModel"
    :permanent="permanent"
    :temporary="temporary"
    :class="drawerClass"
    :color="color"
    :border="border"
    :width="drawerWidth"
    v-bind="$attrs"
  >
    <!-- 窄档（960–1180）的浮层拖右缘改宽度。宽档里侧栏是布局区的一格，拖的是那一格的分隔线；
         两处改的是同一个数（sidebarWidth.ts）。 -->
    <!-- eslint-disable-next-line vuejs-accessibility/no-static-element-interactions -- a drag handle for the pointer; without it the sidebar keeps its width -->
    <div
      v-if="isDesktop && compactSidebar"
      class="sidebar-resizer"
      :title="t('navigation.sidebar.resize')"
      @pointerdown="startResize"
    />
    <!-- 桌面上侧栏头和侧栏底：项目切换器、搜索和「首页」那一行，头像 + 问芝士（appNav.ts）。
         四处侧栏（项目、首页、空间、后台）都长这两段，左边那一列拿掉后它们住在这里；首页那一格
         的侧栏不画「首页」那一行（`homeArea`）。 -->
    <template v-if="frame" #prepend><component :is="frame.top" :home-area="homeArea" /></template>
    <slot></slot>
    <template v-if="frame" #append><component :is="frame.foot" /></template>
  </v-navigation-drawer>
</template>

<script setup lang="ts">
import { computed, inject, onScopeDispose, ref, watch } from 'vue'
import { useDisplay } from 'vuetify'
import { storeToRefs } from 'pinia'

import { setSidebarWidth, SIDEBAR_CELL_WIDTH, sidebarWidth } from '@/composables/sidebarWidth'
import { useEscapeLayer } from '@/composables/useEscapeStack'
import { useNavigation } from '@/composables/useNavigation'
import { focusSidebarToggle, SIDEBAR_DRAWER_ID, useSidebarCollapse } from '@/composables/useSidebarCollapse'

import { useAppNav } from './appNav'

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
  // 首页那一格的侧栏（HomeSidebarView）：侧栏头已经写着「首页」，不再画下面那一行「首页」
  homeArea?: boolean
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

// 侧栏头和侧栏底那两行只在桌面画；没有外壳（单测单挂一个侧栏）时不画。
const appNav = useAppNav()
const frame = computed(() => (isDesktop.value && appNav ? appNav.frame : null))

// 桌面窄档（960–1180）：这一栏是一只可收起的浮层，顶栏最左那颗开关管它。宽档里它
// 常驻，手机上是另一套（`isSecondaryDrawerOpen`）。
const {
  compact: compactSidebar,
  open: sidebarOpen,
  setExpanded: setSidebarExpanded,
  close: closeSidebar,
} = useSidebarCollapse()

// 宽档里侧栏是外壳布局区的一格（layouts/AppShell.vue），宽度就是那一格的宽度，拖那一格
// 的分隔线来改；窄档的浮层按同一个数打开（sidebarWidth.ts）。手机上是另一套。
const cellWidth = inject(SIDEBAR_CELL_WIDTH, null)
const width = sidebarWidth()
const drawerWidth = computed(() => {
  if (!isDesktop.value) return undefined
  if (!compactSidebar.value && cellWidth?.value) return cellWidth.value
  return width.value
})

// 拖浮层的右缘：宽度 = 指针到抽屉左边缘的距离。
//
// 拖的这段时间里抽屉不走宽度过渡（`page-sidebar--resizing`）：改宽度是每收到一次指针移动
// 就改一次，每次都重新起一段 0.2 秒的过渡，这一栏就永远缓在半路上、追不上指针。
//
// 用指针事件并抓住指针：松手时指针可能已经在窗口外面，那时 mouseup 不会来。抓住指针之后，
// up / cancel 一定回到这个元素上；窗口失焦那一下也补一条。拖动中途不写本地存储，只改这一个
// 数；松手才记一次。
const resizing = ref(false)
let stopDrag: (() => void) | null = null

function startResize(e: PointerEvent) {
  if (e.button !== 0) return
  const element = e.currentTarget as HTMLElement
  const left = element.parentElement?.getBoundingClientRect().left ?? 0
  e.preventDefault()
  stopDrag?.()
  try {
    element.setPointerCapture(e.pointerId)
  } catch {
    return
  }
  resizing.value = true
  const move = (ev: PointerEvent) => setSidebarWidth(ev.clientX - left, false)
  const stop = () => {
    window.removeEventListener('pointermove', move)
    window.removeEventListener('pointerup', stop)
    window.removeEventListener('pointercancel', stop)
    window.removeEventListener('blur', stop)
    if (element.hasPointerCapture(e.pointerId)) element.releasePointerCapture(e.pointerId)
    document.body.style.cursor = ''
    document.body.style.userSelect = ''
    resizing.value = false
    setSidebarWidth(width.value)
    stopDrag = null
  }
  stopDrag = stop
  window.addEventListener('pointermove', move)
  window.addEventListener('pointerup', stop)
  window.addEventListener('pointercancel', stop)
  window.addEventListener('blur', stop)
  document.body.style.cursor = 'col-resize'
  document.body.style.userSelect = 'none'
}

// 拖到一半这一栏被换掉（换路由、换档）：别把监听留下。
onScopeDispose(() => stopDrag?.())

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
    // 收起，不是人的选择，所以不写进 localStorage：记住的只该是顶栏那颗开关
    // 按下的那一下（`toggle()` 才 persist）。
    if (isDesktop.value) setSidebarExpanded(value, false)
    else navigationStore.setSecondaryDrawerOpen(value)
  },
})

// 窄档里这一栏是一只可收起的浮层：它开着的时候，按 Esc 关掉它、焦点回到打开它那颗开关
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
  if (frame.value) classes.push('page-sidebar--framed')
  if (resizing.value) classes.push('page-sidebar--resizing')
  return classes.join(' ')
})

// 路由变化时自动关闭移动端抽屉。每次跳转都是一个新快照（`useNavigation` 那个
// computed 跟着 `$route` 走），所以看它的身份就等于看「有没有跳转」，不必拼 fullPath。
const navigation = useNavigation()
watch(
  () => navigation?.route,
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

<style>
/* 有了侧栏头和侧栏底，中间那段要让出它们的高度：Vuetify 给 content 的是 height: 100%，
   不改的话底下那一行被挤出抽屉。 */
.page-sidebar .v-navigation-drawer__content {
  flex: 1 1 auto;
  min-height: 0;
}

/* 侧栏头那一行（ProjectSwitcher）已经画了 48px 基线和底边线；各侧栏自己的标题行
   （空间名、后台分区）跟在它下面，不再画第二条线。 */
/* 拖浮层右缘的这段时间里不走宽度过渡（见 startResize）。 */
.page-sidebar.page-sidebar--resizing {
  transition: none;
}

.page-sidebar--framed .v-navigation-drawer__content .sidebar-header {
  border-block-end: 0;
}
</style>

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
  /* 手指按在这条上拖，别让浏览器把它当成滚页面。 */
  touch-action: none;
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
