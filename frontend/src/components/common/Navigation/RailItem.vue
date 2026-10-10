<template>
  <!-- 项目格子只画头像方块（没挑过头像就是首字母方块），没有任何可见文字，所以链接
       的可访问名称只能来自 aria-label —— 少了它，读屏读不出来，12 个项目里 4 个
       「机」字方块也没法区分。悬停浮层（下面的 v-tooltip）负责鼠标用户；不加原生
       title=，否则悬停会同时冒出浏览器气泡和这个浮层。 -->
  <v-card
    v-if="item.type === 'item'"
    ref="tileRef"
    v-guide-anchor="item.add ? 'rail-add' : ''"
    :to="item.to"
    rounded="lg"
    :border="false"
    class="app-rail-item"
    :aria-label="badgeLabel"
    :data-user-content="projectId ? item.title : undefined"
    :aria-current="current"
    :class="{
      'app-rail-item-cheese': item.icon === 'cheese',
      'app-rail-item--tile': !!item.projectId,
      'app-rail-item--add': item.add,
      'app-rail-item--icon': item.icon && item.icon !== 'cheese' && !item.add && !item.img,
      'app-rail-item--dragging': dragging,
      'app-rail-item--drop-before': dropEdge === 'before',
      'app-rail-item--drop-after': dropEdge === 'after',
    }"
    :draggable="!!projectId"
    :data-project-id="projectId"
    @click="!item.to && item.action ? item.action() : undefined"
    @mouseenter="warmDestination()"
    @mouseleave="cancelPrefetch()"
    @dragstart="onDragStart"
    @dragover="onDragOver"
    @drop="onDrop"
    @dragend="emit('dragEnd')"
    @contextmenu="openMenuAt"
  >
    <!-- 右键弹出的操作（项目格子才有），弹在鼠标那一点上。 -->
    <AdaptiveMenu v-if="menu.length" v-model="menuOpen" :actions="menu" :point="menuPoint" :title="item.title">
      <template #activator />
    </AdaptiveMenu>
    <!-- Discord-style hover flyout: name + quick-switch key (G N in a browser, Cmd N in the desktop app) -->
    <!-- 右键菜单开着时让开：两个浮层都贴在这一格右边，提示会压住菜单的上沿。 -->
    <v-tooltip v-model="flyoutOpen" activator="parent" location="end" content-class="rail-flyout" :disabled="menuOpen">
      <div class="rail-flyout__inner">
        <span class="rail-flyout__name">{{ item.title }}</span>
        <template v-if="item.shortcut">
          <kbd v-for="key in railShortcut(item.shortcut, inDesktopApp()).keys" :key="key" class="rail-flyout__kbd">{{
            key
          }}</kbd>
        </template>
      </div>
    </v-tooltip>

    <template v-if="item.projectId">
      <!-- project tile: the project's own avatar (rounded square, Discord-style).
           交给 UserAvatar 画：挑过头像就上图，没挑过（img 是空串）就退成项目名
           首字母的底色方块。别再这里自己烘一张 SVG data URI —— 那是第二处
           「自己画头像」的地方，颜色还会跟着名字而不是项目走。 -->
      <UserAvatar :avatar="item.img ?? ''" :name="item.title" :seed="item.projectId" kind="org" :size="48" />
    </template>
    <template v-else-if="item.icon === 'cheese'">
      <CheeseLogo width="26" height="26" class="cheese-icon" />
    </template>
    <template v-else-if="item.add">
      <!-- subtle "add" affordance: a plus glyph, no label/color, so it reads as
           a button rather than a project tile -->
      <v-icon size="22" class="app-rail-add-icon">{{ item.icon }}</v-icon>
    </template>
    <template v-else-if="item.icon">
      <v-icon size="24">{{ item.icon }}</v-icon>
    </template>
    <span v-if="badge" class="app-rail-item__badge" aria-hidden="true">{{ badge > 99 ? '99+' : badge }}</span>
    <span v-else-if="dot" class="app-rail-item__dot" aria-hidden="true" />
  </v-card>
  <template v-else>
    <!-- separates 本体(首页) from the project list — a short, visible rule -->
    <v-divider class="app-rail-divider" thickness="2"></v-divider>
  </template>
</template>

<script lang="ts" setup>
import type { DropEdge } from '@/lib/projectOrder'

import { type ComponentPublicInstance, computed, ref, toRefs } from 'vue'
import { useEventListener } from '@vueuse/core'

import { useNavigation } from '@/composables/useNavigation'
import { vGuideAnchor } from '@/composables/useStartGuide'

import { railShortcut } from './destinations'
import { NavGenericItem } from './types'

import CheeseLogo from '@/assets/logo-plain.svg?component'
import AdaptiveMenu from '@/components/common/AdaptiveMenu.vue'
import UserAvatar from '@/components/common/UserAvatar.vue'
import { t } from '@/i18n'
import { inDesktopApp } from '@/lib/desktopApp'
import { cancelPrefetch, prefetchOnHover } from '@/lib/routePrefetch'

// 自定义的拖拽类型，不是 text/plain：rail 只接自己格子拖过来的东西，从桌面拖一个
// 文件或从别的标签页拖一段文字进来时不该有任何反应。
const DRAG_TYPE = 'application/x-cheese-project'

const navBarProps = defineProps<{
  item: NavGenericItem
  /** 这一格正被拖着。 */
  dragging?: boolean
  /** 插入线画在这一格的哪一边；不是落点就是 null。 */
  dropEdge?: DropEdge | null
}>()

const emit = defineEmits<{
  dragStart: [projectId: string]
  dragOver: [projectId: string, edge: DropEdge]
  drop: [movedId: string, targetId: string, edge: DropEdge]
  dragEnd: []
}>()

const { item } = toRefs(navBarProps)

const projectId = computed(() => (item.value.type === 'item' ? item.value.projectId : undefined))

const badge = computed(() => (item.value.type === 'item' ? item.value.badge || 0 : 0))
const dot = computed(() => item.value.type === 'item' && !badge.value && !!item.value.dot)
// 角标和小点都是画给眼睛的（aria-hidden），读屏从名字里听到件数或「有新动态」。
const badgeLabel = computed(() => {
  if (item.value.type !== 'item') return undefined
  if (badge.value) return t('global.labelWithAside', { label: item.value.title, aside: badge.value })
  if (dot.value) return t('global.labelWithAside', { label: item.value.title, aside: t('home.nav.unreadActivity') })
  return item.value.title
})

// 一格底下住着好几条并列的路由时（首页那一格：待办、团队、空间），由它自己说
// 哪些地址算「正待着」；其余格子照旧交给链接自己判断。
const nav = useNavigation()
const current = computed(() => {
  if (item.value.type !== 'item' || !item.value.match) return undefined
  return item.value.match(nav?.route?.path ?? '') ? 'page' : undefined
})

// v-tooltip 内部写死了 persistent，点别处、按 Esc 都关不掉它，能关它的只有这一格自己
// 的 mouseleave 和失焦，而这两样都会落空：键盘焦点打开的浮层，指针怎么移都不关；rail
// 被 keep-alive 收起时浮层留在 <body> 里，再也等不到这一格的事件；浏览器丢一次
// mouseleave 也一样。所以浮层开着的时候盯住指针，它在这一格之外一动，浮层就收起。
const tileRef = ref<ComponentPublicInstance>()
const flyoutOpen = ref(false)
useEventListener(
  () => (flyoutOpen.value ? document : null),
  'pointermove',
  (e: PointerEvent) => {
    const tile = tileRef.value?.$el as Element | undefined
    if (!tile?.contains(e.target as Node)) flyoutOpen.value = false
  },
  { passive: true }
)

const menu = computed(() => (item.value.type === 'item' ? item.value.menu ?? [] : []))
const menuOpen = ref(false)
const menuPoint = ref<[number, number] | null>(null)
function openMenuAt(e: MouseEvent) {
  if (!menu.value.length) return
  e.preventDefault()
  menuPoint.value = [e.clientX, e.clientY]
  menuOpen.value = true
}

function onDragStart(e: DragEvent) {
  const id = projectId.value
  if (!id || !e.dataTransfer) return
  // 项目格子有 `to`，所以它渲染出来是个 <a> —— 而 <a href> 本来就可以拖，拖的是那条
  // 链接。写进自己的类型，拖的才是这一格。
  e.dataTransfer.setData(DRAG_TYPE, id)
  e.dataTransfer.effectAllowed = 'move'
  emit('dragStart', id)
}

function onDragOver(e: DragEvent) {
  // dragover 里 getData() 一定是空的（拖放的安全限制只放行 types），所以认类型。
  if (!projectId.value || !e.dataTransfer?.types.includes(DRAG_TYPE)) return
  // 不 preventDefault 就没有 drop 事件——浏览器默认「这里不收」。
  e.preventDefault()
  e.dataTransfer.dropEffect = 'move'
  // 落在上半还是下半决定插在前面还是后面。只认「插在某一格前面」的话，一列项目的
  // 最末位置就永远够不着。
  const box = (e.currentTarget as HTMLElement).getBoundingClientRect()
  emit('dragOver', projectId.value, e.clientY < box.top + box.height / 2 ? 'before' : 'after')
}

function onDrop(e: DragEvent) {
  const moved = e.dataTransfer?.getData(DRAG_TYPE)
  const edge = navBarProps.dropEdge
  if (!moved || !projectId.value || !edge) return
  e.preventDefault()
  emit('drop', moved, projectId.value, edge)
}

// 一级导航的每一格都是整整一个页面。指针停在格子上的那几百毫秒，正好够把那个页面
// 的代码下下来——按下去的时候就只剩下拉数据那一段了。预取要的是整台 router（拿它
// 解析出要下的 chunk），就从 `nav.router` 上取；宿主没装路由时它整份是 null，没有
// 要预热的目的地，也就什么都不做。
function warmDestination() {
  const to = item.value.type === 'item' ? item.value.to : undefined
  if (to) prefetchOnHover({ router: nav?.router, to })
}
</script>

<style lang="scss">
.app-rail-item {
  position: relative;
  width: 48px;
  height: 48px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  --app-rail-item-background: 0.67;
  background-color: rgba(var(--v-theme-surface-light), var(--app-rail-item-background));
  color: rgba(var(--v-theme-on-surface), var(--v-medium-emphasis-opacity));
  transition:
    background-color var(--dur-quick) var(--ease-standard),
    border-color var(--dur-quick) var(--ease-standard),
    color var(--dur-quick) var(--ease-standard);
  gap: 2px;

  &:hover {
    --app-rail-item-background: 0.87;
    background-color: rgba(var(--v-theme-surface-light), var(--app-rail-item-background));
    color: rgba(var(--v-theme-on-surface), var(--v-high-emphasis-opacity));
    cursor: pointer;
  }

  &.app-rail-item-cheese {
    // a clearly visible rounded-square tile (iOS-app-icon style, per Image #63)
    // so 知是's home icon reads as a 方块 — the 4 corners must show fill around
    // the round logo, not let the logo fill the slot into a circle.
    //
    // Ink-at-low-alpha instead of the old literal #e2e4ea, which was a fixed
    // near-white and turned into a glaring bright patch on the dark rail. 12% of
    // --on-surface over the rail's --background reproduces the light tile almost
    // exactly (#E0E1E3 vs the old #E2E4EA, 1.20:1 against the canvas) and gives
    // the dark theme its own ≈#2B2C2E tile at 1.31:1 — so the 方块 stays legible
    // in both, which a single literal cannot do.
    background-color: rgba(var(--v-theme-on-surface), 0.12);
    // v-card 默认 overflow: hidden，而且沿着圆角裁：探出右上角的未读点和件数角标
    // 会被切掉一块。别的格子早就放开了（--tile / --icon），这一格漏了。
    overflow: visible;

    .cheese-icon {
      // logo size set via CSS (the width/height props on the ?component SVG don't
      // reliably apply). The mark is a solid disc: at 32px and near-full ink it
      // was the heaviest thing in the rail, and an unselected home tile read as
      // the selected one. So it sits a size down and faded until the tile is
      // hovered or current, where the brand paint below takes over. The ink is
      // on-surface, so "faded" means lighter in light theme and dimmer in dark.
      width: 26px !important;
      height: 26px !important;
      fill: rgb(var(--v-theme-on-surface));
      opacity: 0.45;
      transition:
        fill var(--dur-quick) var(--ease-standard),
        opacity var(--dur-quick) var(--ease-standard);
    }

    // Brand paint. These two literals are deliberate and identical in both
    // themes: the 知是 mark is the brand, not a surface, so it must not shift
    // with the theme any more than a printed logo would. What DID have to
    // change is the ink on top — it used to be `on-primary`, a value Vuetify
    // derives from `primary`, which differs between the themes (#F57F17 vs the
    // lightened #FFA733) and could flip the glyph to white on this bright
    // amber. Pinning it to one dark ink keeps the logo at 7.7:1 against the
    // brand colour, in BOTH themes.
    &:hover,
    &[aria-current] {
      background: #ffa20f;

      .cheese-icon {
        fill: #23242a;
        opacity: var(--v-high-emphasis-opacity);
      }
    }
  }
}

// 拖着换顺序：被拖的那一格淡下去，落点那一格画一条插入线。线用中性的
// on-surface，不用琥珀——琥珀留给唯一的主操作、当前选中的导航格和品牌标记。
.app-rail-item--dragging {
  opacity: 0.4;
}

.app-rail-item--drop-before,
.app-rail-item--drop-after {
  &::after {
    content: '';
    position: absolute;
    inset-inline: 0;
    height: 2px;
    background-color: rgba(var(--v-theme-on-surface), 0.55);
    pointer-events: none;
  }
}

.app-rail-item--drop-before::after {
  top: -3px;
}

.app-rail-item--drop-after::after {
  bottom: -3px;
}

// project tiles: the colored rounded-square IS the visual (like the 元思 app
// icon) and it fills the whole 48px slot — no card box and no gap around it,
// or the tile reads as a small square framed inside a bigger one.
.app-rail-item.app-rail-item--tile {
  background-color: transparent;
  // v-card 默认 overflow: hidden，会把探出格子的件数角标和左边的竖条裁掉。
  overflow: visible;

  &:hover,
  &[aria-current] {
    background-color: transparent;
  }
}

// 「你在这儿」画在 rail 左边缘的一条圆头竖条上（Discord 式），不碰头像，也不和
// 右上角的件数角标打架：选中是长的一条，悬停没选中的格子是短的一截。原来的琥珀
// 描边框在头像外面再套一圈，和头像自己的颜色混在一起，不好看也不好认。
//
// 格子 48px 居中在 64px 的 rail 里，左边离 rail 边缘 8px，所以竖条往外挪 8px 贴边。
// 颜色用 on-surface：深色 rail 上是白的，浅色 rail 上是深墨，同一个 token 两套
// 主题都够亮眼；不用琥珀，琥珀留给主操作和品牌标记。「＋新建项目」不是目的地，
// 不画竖条。
.app-rail-item:not(.app-rail-item--add)::before {
  content: '';
  position: absolute;
  left: -8px;
  top: 50%;
  width: 4px;
  height: 0;
  border-radius: var(--radius-pill);
  background-color: rgb(var(--v-theme-on-surface));
  transform: translateY(-50%);
  transition: height var(--dur-quick) var(--ease-standard);
  pointer-events: none;
}

.app-rail-item:not(.app-rail-item--add):hover::before {
  height: 20px;
}

.app-rail-item:not(.app-rail-item--add)[aria-current]::before {
  height: 36px;
}

// 普通图标格（「待办」）：和首页那一格同一块底，选中时用琥珀色的图标说「你在这儿」。
.app-rail-item.app-rail-item--icon {
  overflow: visible;

  &[aria-current] {
    color: rgb(var(--v-theme-primary));
  }
}

// 件数角标：和看板、待办页那颗「待处理」标记同一个暖色。外圈一道 rail 底色，
// 让它压在格子角上时边缘是清楚的。字要一块**不跟着主题翻白**的深色：白字压在
// 这个暖色上只有 2.4:1，而 --ink 在深色主题下正好是白的。--inverse-surface 就是
// 这块深墨（浅色 #23242A / 深色 #3A3D44），压在 --warn 上量出来 6.2:1 与 5.4:1，
// 两套主题都过 4.5:1。
.app-rail-item__dot {
  position: absolute;
  top: -2px;
  right: -2px;
  width: 10px;
  height: 10px;
  border-radius: var(--radius-pill);
  background: var(--warn);
  box-shadow: 0 0 0 2px var(--canvas);
  pointer-events: none;
}

.app-rail-item__badge {
  position: absolute;
  top: -4px;
  right: -4px;
  min-width: 18px;
  height: 18px;
  padding: 0 5px;
  border-radius: var(--radius-pill);
  background: var(--warn);
  box-shadow: 0 0 0 2px var(--canvas);
  color: var(--inverse-surface);
  font-size: 12px;
  font-weight: 600;
  line-height: 18px;
  text-align: center;
  pointer-events: none;
}

// "add project" affordance: a dashed rounded square with a muted plus, distinct
// from a project tile (no colored avatar). Hover stays neutral: it is not the
// page's primary action, so it gets no amber.
.app-rail-item.app-rail-item--add {
  background-color: transparent;
  border: 1.5px dashed rgba(var(--v-theme-on-surface), 0.28);
  color: rgba(var(--v-theme-on-surface), var(--v-medium-emphasis-opacity));

  &:hover {
    background-color: var(--fill-2);
    border-color: var(--line-2);
    color: var(--text);
  }
}

.app-rail-divider.app-rail-divider {
  // clear gap above/below + a solid enough rule that the boundary between
  // 本体(首页) and the project list reads at a glance
  width: 24px;
  margin: 6px auto;
  opacity: 0.6;
  border-radius: var(--radius-pill);
}

/* Discord-style hover flyout. The inverse block itself is every v-tooltip's
   (style.css); the rail's one is a size up — it names a whole destination and
   carries a shortcut. Rendered at the <body>, so this is global (the style block
   is unscoped). */
.v-tooltip.v-tooltip > .v-overlay__content.rail-flyout {
  padding: 8px 12px;
  font-size: 14px;
  font-weight: 600;
  line-height: var(--lh-14);
  box-shadow: var(--shadow-2);
}
.rail-flyout .rail-flyout__inner {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
.rail-flyout .rail-flyout__kbd {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 20px;
  height: 20px;
  padding: 0 4px;
  border-radius: var(--radius-sm);
  background: var(--inverse-fill);
  color: var(--inverse-ink);
  font-size: 12px;
  font-weight: 600;
  font-family: inherit;
}
</style>
