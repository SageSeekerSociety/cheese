<template>
  <!-- `background` (not a fixed grey): the rail is on every page, so a Material
       palette name like grey-lighten-5 would pin it to #FAFAFA in dark theme
       while its icons follow --v-theme-on-surface → white tile, pale icons. -->
  <v-navigation-drawer
    permanent
    rail
    :rail-width="64"
    class="app-rail pb-2"
    color="background"
    border="none"
    @dragover="onRailDragOver"
    @drop="onRailDrop"
  >
    <!-- <v-avatar v-tooltip="'知是'" :image="logo" size="48" /> -->
    <RailItem
      v-for="item in items"
      :key="item.key"
      :item="item"
      :dragging="isDragging(item)"
      :drop-edge="dropEdgeFor(item)"
      @drag-start="draggingId = $event"
      @drag-over="aimAt"
      @drop="finishDrag"
      @drag-end="clearDrag"
    ></RailItem>
    <v-spacer></v-spacer>
    <v-menu
      v-if="userMenu.loggedIn.value"
      v-model="userMenu.menuOpen.value"
      open-on-click
      location="top start"
      :offset="16"
      transition="scale-transition"
    >
      <template #activator="{ props }">
        <v-avatar
          v-tooltip="userMenu.nickname.value"
          class="cursor-pointer elevation-1 mb-4"
          size="32"
          :style="userMenu.avatar.value ? undefined : { backgroundColor: userMenu.avatarColor.value }"
          v-bind="props"
        >
          <v-img v-if="userMenu.avatar.value" :src="userMenu.avatar.value">
            <!-- avatar service (localhost:8081) may be down in the merged demo:
                 fall back to a colored initial instead of a broken white tile -->
            <template #error>
              <span class="rail-avatar-char" :style="{ backgroundColor: userMenu.avatarColor.value }">{{
                userMenu.avatarInitial.value
              }}</span>
            </template>
          </v-img>
          <span v-else class="rail-avatar-char">{{ userMenu.avatarInitial.value }}</span>
        </v-avatar>
      </template>

      <v-card class="user-menu-card rounded-lg elevation-1 border pa-0" min-width="300">
        <v-card-item class="user-header pa-4 pb-3">
          <v-avatar
            size="56"
            class="mb-2"
            elevation="1"
            :style="userMenu.avatar.value ? undefined : { backgroundColor: userMenu.avatarColor.value }"
          >
            <v-img v-if="userMenu.avatar.value" :src="userMenu.avatar.value">
              <template #error>
                <span
                  class="rail-avatar-char rail-avatar-char--lg"
                  :style="{ backgroundColor: userMenu.avatarColor.value }"
                  >{{ userMenu.avatarInitial.value }}</span
                >
              </template>
            </v-img>
            <span v-else class="rail-avatar-char rail-avatar-char--lg">{{ userMenu.avatarInitial.value }}</span>
          </v-avatar>
          <div>
            <v-card-title class="px-0 py-0 text-h6 font-weight-bold">{{ userMenu.nickname.value }}</v-card-title>
            <v-card-subtitle class="px-0 pt-1 pb-0 text-body-2 text-medium-emphasis text-truncate" max-width="220">
              {{ userMenu.intro.value || '还没有个人简介' }}
            </v-card-subtitle>
          </div>
          <div class="d-flex mt-2">
            <v-chip prepend-icon="mdi-account" color="primary" variant="outlined" density="comfortable" size="small">
              UID: {{ userMenu.currentUser.value?.id }}
            </v-chip>
          </div>
        </v-card-item>

        <v-divider></v-divider>

        <v-card-text class="px-4 py-4">
          <v-card variant="tonal" color="primary" class="ai-quota-card rounded-lg mb-3" elevation="0">
            <v-card-text class="pa-3">
              <div class="d-flex align-center mb-2">
                <!-- surface-bright, not white: this disc sits inside a tonal
                     card, so a hard #FFF would be a glaring hole in dark theme. -->
                <v-avatar color="surface-bright" size="28" class="me-2">
                  <v-icon icon="mdi-creation" color="primary" size="small"></v-icon>
                </v-avatar>
                <span class="text-subtitle-2 font-weight-medium">知启星 AI</span>
              </div>

              <div class="d-flex justify-space-between align-center text-body-2 mb-2">
                <span>今日剩余额度</span>
                <span class="font-weight-medium">
                  {{ userMenu.aiQuota.value?.remaining ?? '-' }}/{{ userMenu.aiQuota.value?.daily ?? '-' }}
                </span>
              </div>

              <v-progress-linear
                :model-value="
                  userMenu.aiQuota.value ? (userMenu.aiQuota.value.remaining / userMenu.aiQuota.value.daily) * 100 : 0
                "
                color="primary"
                bg-color="primary-lighten-5"
                height="4"
                rounded
              ></v-progress-linear>

              <div class="text-caption mt-1">
                将在
                {{ userMenu.aiQuota.value ? userMenu.dayjs(userMenu.aiQuota.value.resetTime).fromNow() : '-' }} 重置
              </div>
            </v-card-text>
          </v-card>

          <v-list class="user-menu-list pa-0" rounded="lg" elevation="0">
            <v-list-item
              :to="{ name: 'UserDefault', params: { id: userMenu.currentUser.value?.id } }"
              rounded="lg"
              class="mb-1"
              color="primary"
            >
              <template #prepend>
                <v-icon icon="mdi-account" class="me-2"></v-icon>
              </template>
              <v-list-item-title>个人中心</v-list-item-title>
            </v-list-item>
            <v-list-item :to="{ name: 'my-devices' }" rounded="lg" class="mb-1" color="primary">
              <template #prepend>
                <v-icon icon="mdi-server-network" class="me-2"></v-icon>
              </template>
              <v-list-item-title>我的设备</v-list-item-title>
            </v-list-item>
            <ThemeToggle />
            <v-list-item to="/about" rounded="lg" class="mb-1" color="primary">
              <template #prepend>
                <v-icon icon="mdi-information-outline" class="me-2"></v-icon>
              </template>
              <v-list-item-title>了解知是</v-list-item-title>
            </v-list-item>
            <v-list-item rounded="lg" color="error" @click="userMenu.onLogout">
              <template #prepend>
                <v-icon icon="mdi-exit-to-app" class="me-2"></v-icon>
              </template>
              <v-list-item-title>退出登录</v-list-item-title>
            </v-list-item>
          </v-list>
        </v-card-text>
      </v-card>
    </v-menu>
    <v-btn
      v-else
      to="/account/signin"
      variant="text"
      color="on-surface-variant"
      width="48"
      height="48"
      min-width="0"
      stacked
      class="pa-0"
    >
      <template #prepend>
        <v-icon icon="mdi-login" size="20" />
      </template>
      登录
    </v-btn>
  </v-navigation-drawer>
</template>

<script setup lang="ts">
import { ref, toRefs } from 'vue'

import { useUserMenu } from '@/composables/useUserMenu'

import RailItem from './RailItem.vue'
import { NavBarProps, NavGenericItem } from './types'

import logo from '@/assets/logo.svg?url'
import ThemeToggle from '@/components/common/ThemeToggle.vue'
import { type DropEdge, dropTargetAt } from '@/lib/projectOrder'

const navBarProps = withDefaults(defineProps<NavBarProps>(), {
  items: () => [],
})

const { items } = toRefs(navBarProps)

const emit = defineEmits<{
  /** 把 `movedId` 放到 `targetId` 的这一边。顺序归 App.vue 保管，rail 只报告动作。 */
  reorder: [movedId: string, targetId: string, edge: DropEdge]
}>()

// 拖拽的状态住在 rail 上而不是每个格子里：画插入线的那一格和被拖走的那一格不是
// 同一个，两边都得知道现在拖的是谁。
const draggingId = ref<string | null>(null)
const dropTarget = ref<{ id: string; edge: DropEdge } | null>(null)

const projectIdOf = (item: NavGenericItem) => (item.type === 'item' ? item.projectId : undefined)

const isDragging = (item: NavGenericItem) => !!draggingId.value && projectIdOf(item) === draggingId.value

function dropEdgeFor(item: NavGenericItem): DropEdge | null {
  const id = projectIdOf(item)
  return id && dropTarget.value?.id === id ? dropTarget.value.edge : null
}

function aimAt(projectId: string, edge: DropEdge) {
  // 拖着的那一格自己不画线——那是个原地不动的落点。
  dropTarget.value = projectId === draggingId.value ? null : { id: projectId, edge }
}

// 一列格子之间的空白、分隔线、首页那一格——这些地方以前既不收货也不撤销插入线：
// 线还画着「放这儿就插到最前」，松手却什么都不发生。而「挪到最前面」这个动作，手
// 势天然会往上多走一点，正好走进那片只画线不收货的区域，所以最前面那一格是唯一
// 真的挪不过去的位置。
//
// 指针停在某个格子上时那一格自己已经处理过并 preventDefault 了，所以这里只管它没
// 处理的部分：第一格上方就是插到最前，最后一格下方就是插到最后。
const DRAG_TYPE = 'application/x-cheese-project'

function projectTiles(event: DragEvent): HTMLElement[] {
  return Array.from((event.currentTarget as HTMLElement).querySelectorAll<HTMLElement>('[data-project-id]'))
}

function onRailDragOver(event: DragEvent) {
  if (event.defaultPrevented) return
  if (!event.dataTransfer?.types.includes(DRAG_TYPE)) return
  const spans = projectTiles(event)
    .map((tile) => ({ id: tile.dataset.projectId ?? '', ...tile.getBoundingClientRect() }))
    .filter((span) => span.id)
  const aim = dropTargetAt(spans, event.clientY)
  if (!aim) return
  // 不 preventDefault 就没有 drop 事件——浏览器默认「这里不收」。
  event.preventDefault()
  event.dataTransfer.dropEffect = 'move'
  aimAt(aim.id, aim.edge)
}

function onRailDrop(event: DragEvent) {
  if (event.defaultPrevented) return
  const moved = event.dataTransfer?.getData(DRAG_TYPE)
  const target = dropTarget.value
  if (!moved || !target) return
  event.preventDefault()
  finishDrag(moved, target.id, target.edge)
}

function clearDrag() {
  draggingId.value = null
  dropTarget.value = null
}

function finishDrag(movedId: string, targetId: string, edge: DropEdge) {
  clearDrag()
  if (movedId !== targetId) emit('reorder', movedId, targetId, edge)
}

// 使用用户菜单 composable
const userMenu = useUserMenu()
</script>

<style lang="scss">
.app-rail {
  .v-navigation-drawer__content {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: flex-start;
    gap: 8px;
  }
}

.logo {
  width: 48px;
  height: 48px;
}

/* Colored-initial fallback for the default user avatar (no uploaded image). */
.rail-avatar-char {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 100%;
  line-height: 1;
  // Deliberately a literal, and one of the few that is CORRECT in both themes:
  // the background here is not a theme token but the `#rrggbb` that
  // `avatarColor()` computes — a fixed PERCEPTUAL lightness (OKLCH L = 0.54 /
  // C = 0.12), identical in light and dark. This is "on-avatar" ink, so it must
  // not follow --v-theme-on-surface (that would turn it near-black on light and
  // pale-grey on dark, over the same colour). Do not "fix" it to a token.
  // The contrast caveat this comment used to carry is gone: the old
  // hsl(h, 55%, 55%) formula dropped to ~1.7:1 on yellow/green hues, while the
  // OKLCH one lands every hue between 4.75:1 and 5.43:1 against this white —
  // asserted hue-by-hue in src/utils/avatar.spec.ts.
  color: #fff;
  font-weight: 600;
  font-size: 14px;

  &--lg {
    font-size: 22px;
  }
}
</style>
