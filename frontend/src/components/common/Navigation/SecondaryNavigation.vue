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
    <!-- eslint-disable-next-line vuejs-accessibility/no-static-element-interactions -- a drag handle for the pointer; without it the sidebar keeps its width -->
    <div v-if="isDesktop" class="sidebar-resizer" :title="t('navigation.sidebar.resize')" @pointerdown="startResize" />
    <slot></slot>
  </v-navigation-drawer>
</template>

<script setup lang="ts">
import { computed, onScopeDispose, watch } from 'vue'
import { useDisplay } from 'vuetify'
import { storeToRefs } from 'pinia'

import { useEscapeLayer } from '@/composables/useEscapeStack'
import { useNavigation } from '@/composables/useNavigation'
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
//
// 拖的这段时间里要关掉过渡（`html.sidebar-resizing`，规则在 styles/common.scss）。
// 抽屉的宽度和内容区的左内边距各带 Vuetify 的 0.2 秒过渡：改宽度是每收到一次指针
// 移动就改一次，每次都重新起一段过渡，于是这一栏永远缓在半路上、追不上指针，松手
// 后还要再滑完剩下那段。Vuetify 自己用手指拖抽屉时也是这么关的。
//
// 用指针事件并抓住指针：松手时指针可能已经在窗口外面（拖到屏幕边上、拖出浏览器），
// 那时 mouseup 不会来，过渡就一直关着——整个应用从此没有动画。抓住指针之后，
// up / cancel 一定回到这个元素上。窗口失焦那一下也补一条。
//
// 拖动中途不写本地存储，只改这一个数：每动一下写一次是同步的磁盘写入，跟着指针
// 一起卡。松手才把落定的宽度记一次。
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
  document.documentElement.classList.add('sidebar-resizing')
  const move = (ev: PointerEvent) => setWidth(ev.clientX - left, false)
  const stop = () => {
    window.removeEventListener('pointermove', move)
    window.removeEventListener('pointerup', stop)
    window.removeEventListener('pointercancel', stop)
    window.removeEventListener('blur', stop)
    if (element.hasPointerCapture(e.pointerId)) element.releasePointerCapture(e.pointerId)
    document.body.style.cursor = ''
    document.body.style.userSelect = ''
    document.documentElement.classList.remove('sidebar-resizing')
    // 松手把最后落在的那个宽度记一次；这一下没拖动过就是原来那个，等于没写。
    setWidth(width.value)
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

// 拖到一半这一栏被换掉（换路由、切到手机布局）：别把监听和 html 上那个类留下。
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
