<template>
  <v-card
    v-if="item.type === 'item'"
    :to="item.to"
    rounded="lg"
    class="app-rail-item"
    :class="{
      'app-rail-item-cheese': item.icon === 'cheese',
      'app-rail-item--tile': item.img,
      'app-rail-item--add': item.add,
    }"
    @click="!item.to && item.action ? item.action() : undefined"
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

import { NavGenericItem } from './types'

import CheeseLogo from '@/assets/logo-plain.svg?component'

const navBarProps = defineProps<{
  item: NavGenericItem
}>()

const { item } = toRefs(navBarProps)
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
    // a clearly visible light-grey rounded-square tile (iOS-app-icon style, per
    // Image #63) so 知是's home icon reads as a 方块 — the 4 corners must show grey
    // around the round logo, not let the logo fill the slot into a circle.
    background-color: #e2e4ea;

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

    &:hover {
      background: linear-gradient(to bottom, #ff9500, #ffe600);

      .cheese-icon {
        fill: rgb(var(--v-theme-on-primary));
        opacity: var(--v-high-emphasis-opacity);
      }
    }

    &[aria-current] {
      background: linear-gradient(to bottom, #ff9500, #ffe600);

      .cheese-icon {
        fill: rgb(var(--v-theme-on-primary));
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
    box-shadow: 0 0 0 3px rgb(var(--v-theme-surface)), 0 0 0 5px #f57f17;
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
   (not Discord's white), so the selected rail tile reads at a glance */
.app-rail-item[aria-current]::before {
  content: '';
  position: absolute;
  left: -10px;
  top: 50%;
  transform: translateY(-50%);
  width: 4px;
  height: 22px;
  border-radius: 0 3px 3px 0;
  background: #f57f17;
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
}
.rail-flyout .rail-flyout__inner {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  background: #23242a;
  color: #fff;
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
  background: rgba(255, 255, 255, 0.14);
  color: #fff;
  font-size: 12px;
  font-weight: 600;
  font-family: inherit;
}
</style>
