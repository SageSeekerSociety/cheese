<template>
  <!-- 项目格子只画首字方块（projectAvatar），没有任何可见文字，所以链接的可访问
       名称只能来自 aria-label —— 少了它，读屏读不出来，12 个项目里 4 个「机」字
       方块也没法区分。悬停浮层（下面的 v-tooltip）负责鼠标用户；不加原生 title=，
       否则悬停会同时冒出浏览器气泡和这个浮层。 -->
  <v-card
    v-if="item.type === 'item'"
    :to="item.to"
    rounded="lg"
    class="app-rail-item"
    :aria-label="item.title"
    :class="{
      'app-rail-item-cheese': item.icon === 'cheese',
      'app-rail-item--tile': item.img,
      'app-rail-item--add': item.add,
    }"
    @click="!item.to && item.action ? item.action() : undefined"
    @mouseenter="warmDestination()"
    @mouseleave="cancelPrefetch()"
  >
    <!-- Discord-style hover flyout: name + ⌘N quick-switch key -->
    <v-tooltip activator="parent" location="end" content-class="rail-flyout">
      <div class="rail-flyout__inner">
        <span class="rail-flyout__name">{{ item.title }}</span>
        <template v-if="item.shortcut">
          <kbd class="rail-flyout__kbd">⌘</kbd>
          <kbd class="rail-flyout__kbd">{{ item.shortcut }}</kbd>
        </template>
      </div>
    </v-tooltip>

    <template v-if="item.img">
      <!-- project tile: just the colored rounded-square app icon (Discord-style) -->
      <v-img :src="item.img" width="40" height="40" class="rounded-lg" cover />
    </template>
    <template v-else-if="item.icon === 'cheese'">
      <CheeseLogo width="26" height="26" class="cheese-icon" />
    </template>
    <template v-else-if="item.add">
      <!-- subtle "add" affordance: a plus glyph, no label/color, so it reads as
           a button rather than a project tile -->
      <v-icon size="22" class="app-rail-add-icon">{{ item.icon }}</v-icon>
    </template>
    <template v-else>
      <v-icon size="small">{{ item.icon }}</v-icon>
      <div class="text-caption app-rail-item-text">{{ item.title }}</div>
    </template>
  </v-card>
  <template v-else>
    <!-- separates 本体(首页) from the project list — a short, visible rule -->
    <v-divider class="app-rail-divider" thickness="2"></v-divider>
  </template>
</template>

<script lang="ts" setup>
import { toRefs } from 'vue'
import { useRouter } from 'vue-router'

import { NavGenericItem } from './types'

import CheeseLogo from '@/assets/logo-plain.svg?component'
import { cancelPrefetch, prefetchOnHover } from '@/lib/routePrefetch'

const navBarProps = defineProps<{
  item: NavGenericItem
}>()

const { item } = toRefs(navBarProps)

// 一级导航的每一格都是整整一个页面。指针停在格子上的那几百毫秒，正好够把那个页面
// 的代码下下来——按下去的时候就只剩下拉数据那一段了。
const router = useRouter()
function warmDestination() {
  const to = item.value.type === 'item' ? item.value.to : undefined
  if (to) prefetchOnHover({ router, to })
}
</script>

<style lang="scss">
.no-style-link {
  text-decoration: none;
  color: inherit;
}

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
  transition: all 0.2s ease;
  gap: 2px;

  &:hover {
    --app-rail-item-background: 0.87;
    background-color: rgba(var(--v-theme-surface-light), var(--app-rail-item-background));
    color: rgba(var(--v-theme-on-surface), var(--v-high-emphasis-opacity));
    cursor: pointer;
  }

  &[aria-current] {
    --app-rail-item-background: 0.1;
    background-color: rgba(var(--v-theme-primary), var(--app-rail-item-background));
    color: rgba(var(--v-theme-primary), var(--v-high-emphasis-opacity));
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

    .cheese-icon {
      // logo size set via CSS (the width/height props on the ?component SVG don't
      // reliably apply). ~32px in the 48px square ≈ the proportion in Image #63 —
      // the tile is a clear 方块 thanks to the rounded='lg' prop, not by shrinking
      // the logo, so it can sit comfortably large.
      width: 32px !important;
      height: 32px !important;
      fill: rgb(var(--v-theme-on-surface));
      opacity: var(--v-medium-high-opacity);
      transition: all 0.2s ease;
    }

    // Brand paint. These two literals are deliberate and identical in both
    // themes: the 知是 mark is the brand, not a surface, so it must not shift
    // with the theme any more than a printed logo would. What DID have to
    // change is the ink on top — it used to be `on-primary`, a value Vuetify
    // derives from `primary`, which differs between the themes (#F57F17 vs the
    // lightened #FFA733) and could flip the glyph to white on this bright
    // yellow. Pinning it to the same dark ink the flyout uses keeps the logo at
    // 7.0:1 against the #ff9500 stop and 12.2:1 against #ffe600, in BOTH themes.
    &:hover,
    &[aria-current] {
      background: linear-gradient(to bottom, #ff9500, #ffe600);

      .cheese-icon {
        fill: #23242a;
        opacity: var(--v-high-emphasis-opacity);
      }
    }
  }

  .app-rail-item-text {
    line-height: 1;
  }

  // project tiles: keep the name to a single, truncated line so a long title
  // ("知是 2.0 融合演示") never breaks the tile grid
  .app-rail-item-label {
    max-width: 44px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: 10px;
  }
}

// project tiles: the colored rounded-square IS the visual (like the 元思 app
// icon), floating on the rail with its name below — no grey box around it.
.app-rail-item.app-rail-item--tile {
  height: auto;
  min-height: 48px;
  padding: 3px 0 4px;
  gap: 3px;
  background-color: transparent;
  // the amber ring is a box-shadow on the inner 40px avatar; a v-card clips its
  // content (overflow:hidden) so the ring only showed at the corners. Let it
  // render fully so the frame is a clean, symmetric square on all 4 sides.
  overflow: visible;

  // the colored avatar IS the tile — never draw a card box behind it (that made
  // a messy second square around the icon). Active state = the label turns the
  // brand colour instead.
  &:hover,
  &[aria-current] {
    background-color: transparent;
  }
  &[aria-current] .app-rail-item-label {
    color: rgb(var(--v-theme-primary));
    font-weight: 600;
  }

  // Selected project reads as selected via an amber ring FRAMING the square —
  // the 40px avatar keeps a small gap to the ring (box-shadow: 0 spread = the
  // avatar edge, then a transparent gap, then the 2px amber ring).
  .v-img {
    transition: box-shadow 0.2s ease;
  }
  &[aria-current] .v-img {
    // `primary`, not the #f57f17 literal: identical in light (primary IS
    // #F57F17) and correctly lightened to #FFA733 on dark, where the original
    // amber only reaches 3.1:1.
    box-shadow:
      0 0 0 3px rgb(var(--v-theme-surface)),
      0 0 0 5px rgb(var(--v-theme-primary));
  }
}

// "add project" affordance: a dashed rounded square with a muted plus, distinct
// from a project tile (no colored avatar). Greens up on hover to invite the click.
.app-rail-item.app-rail-item--add {
  background-color: transparent;
  border: 1.5px dashed rgba(var(--v-theme-on-surface), 0.28);
  color: rgba(var(--v-theme-on-surface), var(--v-medium-emphasis-opacity));

  .app-rail-add-icon {
    transition: all 0.2s ease;
  }

  &:hover {
    background-color: rgba(var(--v-theme-primary), 0.08);
    border-color: rgba(var(--v-theme-primary), 0.6);
    color: rgba(var(--v-theme-primary), var(--v-high-emphasis-opacity));
  }
}

.app-rail-divider.app-rail-divider {
  // clear gap above/below + a solid enough rule that the boundary between
  // 本体(首页) and the project list reads at a glance
  width: 24px;
  margin: 6px auto;
  opacity: 0.6;
  border-radius: 2px;
}

/* active indicator — a soft amber pill on the left edge, in our brand accent
   (not Discord's white), so the selected rail tile reads at a glance.
   `primary` rather than the #f57f17 literal — same reason as the ring above. */
.app-rail-item[aria-current]::before {
  content: '';
  position: absolute;
  left: -10px;
  top: 50%;
  transform: translateY(-50%);
  width: 4px;
  height: 22px;
  border-radius: 0 3px 3px 0;
  background: rgb(var(--v-theme-primary));
}

/* project tiles use the amber RING (above) as their active indicator, so drop
   the left edge pill for them — the frame around the square carries selection */
.app-rail-item.app-rail-item--tile[aria-current]::before {
  content: none;
}

/* Discord-style hover flyout, tuned to our light/amber aesthetic. Rendered at the
   <body>, so this is global (the style block is unscoped). */
.rail-flyout.rail-flyout {
  background: transparent;
  padding: 0;
  box-shadow: none;
  opacity: 1;

  /* This flyout is an INVERTED element: on light it is deliberately a dark chip
     floating over a pale page. There is no `inverse-surface` token to express
     that (surface/surface-bright are pale on light, which is the opposite), and
     inventing one would ripple into wave 2's mapping — so the two values live
     here as component-local custom properties instead.
     This is the rare, legitimate `[data-theme='dark']` branch: the element is
     not picking the wrong token, it is the one thing that must invert TWICE.
     On dark, #23242a would sink into the #141517 canvas (1.13:1) — a floating
     chip has to be LIGHTER than the page it floats over, hence #3a3d44 (1.68:1
     vs canvas, 1.55:1 vs surface, plus the drop shadow below). */
  --rail-flyout-bg: #23242a;
  --rail-flyout-ink: #fff;
  --rail-flyout-kbd-bg: rgba(255, 255, 255, 0.14);
}
:root[data-theme='dark'] .rail-flyout.rail-flyout {
  --rail-flyout-bg: #3a3d44;
  --rail-flyout-ink: #f3f4f6; /* 9.9:1 on #3a3d44 */
  --rail-flyout-kbd-bg: rgba(255, 255, 255, 0.1);
}
.rail-flyout .rail-flyout__inner {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  background: var(--rail-flyout-bg);
  color: var(--rail-flyout-ink);
  border-radius: 10px;
  font-size: 14px;
  font-weight: 600;
  box-shadow: 0 6px 20px rgba(0, 0, 0, 0.22);
}
.rail-flyout .rail-flyout__kbd {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 20px;
  height: 20px;
  padding: 0 5px;
  border-radius: 5px;
  background: var(--rail-flyout-kbd-bg);
  color: var(--rail-flyout-ink);
  font-size: 12px;
  font-weight: 600;
  font-family: inherit;
}
</style>
