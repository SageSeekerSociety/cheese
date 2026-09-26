<script setup lang="ts">
// 空间新界面的外壳：头部（那块下拉）+ 导航 + 页面。
//
// 与原型 `proto-board/BoardShell.vue` 的差别只有两处，都是「原型才有、真平台不能有」
// 的东西：
// 1. **没有演示身份切换带**。真平台上「我是谁」是登录态，不给人现切 —— 角色从
//    `store.ts` 的 `role` 来（在不在管理员名单里）。
// 2. 导航用**命名路由**而不是写死的 `/`、`/mine`：这一棵挂在 `/spaces/:id/board`
//    下面，路径里带着空间 id，写死字符串会把人送到别处去。
//
// 还没接上的入口（邀请码的改码、编辑信息）先指向**真平台已有的那些页**，不装作
// 新界面已经能改 —— 它们各自的接口还没落地（见任务的分批说明）。
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { ROLE_LABEL } from './model'
import { failed, isManager, isOwner, loadBoard, me, role, space } from './store'

const props = defineProps<{ spaceId: number }>()

const route = useRoute()
const router = useRouter()

const menuOpen = ref(false)

const spaceName = computed(() => space.value?.name ?? '空间')

const NAV = computed(() =>
  [
    { name: 'SpaceBoardHome', label: '空间', icon: 'mdi-view-grid-outline', show: true },
    { name: 'SpaceBoardMine', label: '我的', icon: 'mdi-account-outline', show: true },
    { name: 'SpaceBoardAnnouncements', label: '公告', icon: 'mdi-bullhorn-outline', show: true },
    { name: 'SpaceBoardReview', label: '审核', icon: 'mdi-clipboard-check-outline', show: isManager.value },
    { name: 'SpaceBoardMembers', label: '成员', icon: 'mdi-account-group-outline', show: isManager.value },
    { name: 'SpaceBoardAnalytics', label: '数据看板', icon: 'mdi-chart-box-outline', show: isManager.value },
  ].filter((i) => i.show)
)

function isActive(name: string) {
  return route.name === name
}

// 从空间 A 换到空间 B 时外壳组件会被复用（同一条路由记录），setup 不会再跑一次，
// 所以换 id 这件事必须靠 watch —— 否则新空间会顶着上一个空间的名字和角色。 */
watch(
  () => props.spaceId,
  (id) => loadBoard(id),
  { immediate: true }
)
</script>

<template>
  <div class="shell">
    <header class="board">
      <div class="board__head">
        <!-- 有管理权：头像 + 名字 + 箭头，点开就是那块下拉。 -->
        <v-menu v-if="isManager" v-model="menuOpen" offset="8" location="bottom start">
          <template #activator="{ props: activator }">
            <button class="board__title board__title--clickable" v-bind="activator" type="button">
              <v-avatar size="28" rounded="lg" class="board__avatar">
                <v-icon icon="mdi-image-outline" size="16" />
              </v-avatar>
              <span class="board__name">{{ spaceName }}</span>
              <v-icon :icon="menuOpen ? 'mdi-chevron-up' : 'mdi-chevron-down'" size="20" />
            </button>
          </template>

          <v-list density="compact" min-width="240" rounded="lg" class="menu">
            <v-list-item
              prepend-icon="mdi-pencil"
              title="编辑信息"
              subtitle="名称、简介、封面"
              @click="(menuOpen = false), router.push({ name: 'SpacesDetail', params: { spaceId } })"
            />
            <v-divider class="my-1" />
            <v-list-item
              prepend-icon="mdi-ticket-confirmation-outline"
              title="邀请码"
              subtitle="改用已有页面管理"
              @click="(menuOpen = false), router.push({ name: 'SpacesDetailManageInviteCodes', params: { spaceId } })"
            />
            <v-list-item
              prepend-icon="mdi-account-multiple-outline"
              title="成员与角色"
              @click="(menuOpen = false), router.push({ name: 'SpaceBoardMembers', params: { spaceId } })"
            />
            <v-divider class="my-1" />
            <v-list-item
              v-if="isOwner"
              prepend-icon="mdi-account-cog"
              title="管理员设置"
              subtitle="谁可以管理这个空间"
              @click="(menuOpen = false), router.push({ name: 'SpacesDetail', params: { spaceId } })"
            />
            <v-list-item v-else prepend-icon="mdi-shield-account-outline" title="管理员设置" disabled>
              <template #subtitle>只有所有者能改</template>
            </v-list-item>
          </v-list>
        </v-menu>

        <!-- 普通成员：同一块位置，但没有箭头、点不开。 -->
        <div v-else class="board__title">
          <v-avatar size="28" rounded="lg" class="board__avatar">
            <v-icon icon="mdi-image-outline" size="16" />
          </v-avatar>
          <span class="board__name">{{ spaceName }}</span>
        </div>

        <div class="board__right">
          <v-chip size="small" variant="tonal" label class="board__role">
            {{ me.name }} · {{ ROLE_LABEL[role] }}
          </v-chip>
        </div>
      </div>

      <nav class="board__nav">
        <router-link
          v-for="item in NAV"
          :key="item.name"
          :to="{ name: item.name, params: { spaceId } }"
          class="board__tab"
          :class="{ 'board__tab--active': isActive(item.name) }"
        >
          <v-icon :icon="item.icon" size="18" />
          <span>{{ item.label }}</span>
        </router-link>
      </nav>
    </header>

    <main class="shell__main">
      <!-- 空间读不到时把整页换掉：路由里那几页各自都假设空间已经装好，
           让它们在半装状态下渲染只会得到一堆空表，读起来像「这块板是空的」。 -->
      <v-empty-state
        v-if="failed"
        icon="mdi-lock-question"
        title="打不开这个空间"
        text="它不存在，或者你不在里面。请从空间列表重新进一次。"
      />
      <router-view v-else />
    </main>
  </div>
</template>

<style scoped lang="scss">
.shell {
  display: flex;
  flex-direction: column;
  min-height: 100%;
  background: rgb(var(--v-theme-background));
}

.board {
  background: rgb(var(--v-theme-surface));
  border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.08);
}

.board__head {
  display: flex;
  gap: 16px;
  align-items: center;
  justify-content: space-between;
  padding: 14px 20px 10px;
}

.board__title {
  display: flex;
  gap: 10px;
  align-items: center;
  padding: 6px 10px 6px 6px;
  font: inherit;
  background: transparent;
  border: 1px solid transparent;
  border-radius: var(--radius-md);
}

.board__title--clickable {
  cursor: pointer;
}

.board__title--clickable:hover {
  background: rgba(var(--v-theme-on-surface), 0.05);
}

.board__avatar {
  background: linear-gradient(160deg, rgb(var(--v-theme-primary)), rgb(var(--v-theme-primary-darken-1)));
}

.board__name {
  font-size: 1.02rem;
  font-weight: 600;
}

.board__right {
  display: flex;
  gap: 10px;
  align-items: center;
}

.board__role {
  color: rgba(var(--v-theme-on-surface), 0.7);
}

.board__nav {
  display: flex;
  gap: 4px;
  padding: 0 16px;
  overflow-x: auto;
}

.board__tab {
  display: inline-flex;
  gap: 7px;
  align-items: center;
  padding: 10px 14px;
  color: rgba(var(--v-theme-on-surface), 0.6);
  font-size: 0.86rem;
  text-decoration: none;
  border-bottom: 2px solid transparent;
}

.board__tab:hover {
  color: rgba(var(--v-theme-on-surface), 0.9);
}

.board__tab--active {
  color: rgb(var(--v-theme-on-surface));
  border-bottom-color: rgb(var(--v-theme-primary));
}

.shell__main {
  flex: 1;
  padding: 20px;
}
</style>
