<template>
  <v-card
    v-if="item.type === 'item'"
    :to="item.to"
    class="app-rail-item rounded-lg"
    :class="{ 'app-rail-item-cheese': item.icon === 'cheese', 'app-rail-item--tile': item.img }"
  >
    <template v-if="item.img">
      <!-- match the original 元思 tile: a prominent colored rounded-square app
           icon + the project name as a caption below it (fusion: project tiles) -->
      <v-img :src="item.img" width="40" height="40" class="rounded-lg" cover />
      <div class="text-caption app-rail-item-text app-rail-item-label">
        {{ item.title }}
      </div>
    </template>
    <template v-else-if="item.icon === 'cheese'">
      <CheeseLogo width="30" height="30" class="cheese-icon" />
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
    // a clearly visible light-grey rounded-square tile so 知是's home icon reads
    // as a 方框 (like the original), not just the round logo floating on white
    background-color: #eceef2;

    .cheese-icon {
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
}

.app-rail-divider.app-rail-divider {
  // clear gap above/below + a solid enough rule that the boundary between
  // 本体(首页) and the project list reads at a glance
  width: 24px;
  margin: 6px auto;
  opacity: 0.6;
  border-radius: 2px;
}
</style>
