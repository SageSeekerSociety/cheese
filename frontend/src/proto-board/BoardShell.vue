<script setup lang="ts">
// 原型外壳：一条演示用的身份切换带 + 题目板头部（截图里那块下拉）+ 导航。
//
// 头部下拉是这次重设计的一处落脚点，所以它按**新**的样子做：下拉里除了原有的
// 「编辑信息」「管理员设置」，多了「邀请码」和「成员与角色」，且这些项只对所有者
// 与管理员出现 —— 普通用户看到的是一块不带箭头的静态标题（这一条与今天一致）。
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import InviteCodesDialog from './components/InviteCodesDialog.vue'
import { IDENTITIES, ROLE_LABEL, SPACE } from './fixtures'
import { codes, identity, isManager, isOwner, me, role, switchIdentity } from './store'

const route = useRoute()
const router = useRouter()

const inviteDialog = ref<InstanceType<typeof InviteCodesDialog> | null>(null)
const menuOpen = ref(false)
const adminsDialog = ref(false)

/** 按钮组要的是下标，而 `identity` 是个 computed —— 模板里会自动解包，script 里不会。 */
const identityIndex = computed(() => IDENTITIES.indexOf(identity.value))

const NAV = computed(() =>
  [
    { to: '/', label: '题目板', icon: 'mdi-view-grid-outline', show: true },
    { to: '/mine', label: '我的', icon: 'mdi-account-outline', show: true },
    // 公告不给身份设门槛：普通成员也要看得见板上发过什么，只是发不了。
    { to: '/announcements', label: '公告', icon: 'mdi-bullhorn-outline', show: true },
    { to: '/review', label: '审核', icon: 'mdi-clipboard-check-outline', show: isManager.value, badge: true },
    { to: '/members', label: '成员', icon: 'mdi-account-group-outline', show: isManager.value },
    { to: '/analytics', label: '数据看板', icon: 'mdi-chart-box-outline', show: isManager.value },
  ].filter((i) => i.show)
)

const activeCode = computed(() => codes.value.find((c) => !c.revoked))

function isActive(to: string) {
  return to === '/' ? route.path === '/' : route.path.startsWith(to)
}

function goInvite() {
  menuOpen.value = false
  inviteDialog.value?.show()
}
</script>

<template>
  <div class="shell">
    <!-- 演示带：只有原型才有。真平台上「我是谁」是登录态，不给人现切。 -->
    <div class="demo">
      <span class="demo__tag">原型</span>
      <span class="demo__text">切换身份看不同角色的界面：</span>
      <v-btn-toggle
        :model-value="identityIndex"
        density="compact"
        variant="outlined"
        divided
        mandatory
        @update:model-value="(v) => v !== undefined && switchIdentity(Number(v))"
      >
        <v-btn v-for="(id, i) in IDENTITIES" :key="i" :value="i" size="small" class="demo__btn">
          {{ id.me.name }}
          <span class="demo__role">{{ ROLE_LABEL[id.role] }}</span>
        </v-btn>
      </v-btn-toggle>
      <span class="demo__blurb">{{ identity.blurb }}</span>
    </div>

    <header class="board">
      <div class="board__head">
        <!-- 有管理权：头像 + 名字 + 箭头，点开就是截图那块下拉。 -->
        <v-menu v-if="isManager" v-model="menuOpen" offset="8" location="bottom start">
          <template #activator="{ props: activator }">
            <button class="board__title board__title--clickable" v-bind="activator" type="button">
              <v-avatar size="28" rounded="lg" class="board__avatar">
                <v-icon icon="mdi-image-outline" size="16" />
              </v-avatar>
              <span class="board__name">{{ SPACE.name }}</span>
              <v-icon :icon="menuOpen ? 'mdi-chevron-up' : 'mdi-chevron-down'" size="20" />
            </button>
          </template>

          <v-list density="compact" min-width="240" rounded="lg" class="menu">
            <v-list-item
              prepend-icon="mdi-pencil"
              title="编辑信息"
              subtitle="名称、简介、封面"
              @click="menuOpen = false"
            />
            <v-divider class="my-1" />
            <v-list-item prepend-icon="mdi-ticket-confirmation-outline" title="邀请码" @click="goInvite">
              <template #subtitle>
                <span class="menu__sub">
                  {{ activeCode ? activeCode.code : '暂无' }}
                  <template v-if="activeCode">
                    ·
                    {{
                      activeCode.maxUses === null
                        ? `${activeCode.useCount}/不限`
                        : `${activeCode.useCount}/${activeCode.maxUses}`
                    }}
                  </template>
                </span>
              </template>
            </v-list-item>
            <v-list-item
              prepend-icon="mdi-account-multiple-outline"
              title="成员与角色"
              @click="(menuOpen = false), router.push('/members')"
            />
            <v-divider class="my-1" />
            <!-- 只有所有者能改别人的角色，所以这一项不给管理员看（与今天一致）。 -->
            <v-list-item
              v-if="isOwner"
              prepend-icon="mdi-account-cog"
              title="管理员设置"
              subtitle="谁可以管理这块板"
              @click="(menuOpen = false), (adminsDialog = true)"
            />
            <v-list-item v-else prepend-icon="mdi-shield-account-outline" title="管理员设置" disabled>
              <template #subtitle>只有所有者能改</template>
            </v-list-item>
          </v-list>
        </v-menu>

        <!-- 普通用户：同一块位置，但没有箭头、点不开。 -->
        <div v-else class="board__title">
          <v-avatar size="28" rounded="lg" class="board__avatar">
            <v-icon icon="mdi-image-outline" size="16" />
          </v-avatar>
          <span class="board__name">{{ SPACE.name }}</span>
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
          :key="item.to"
          :to="item.to"
          class="board__tab"
          :class="{ 'board__tab--active': isActive(item.to) }"
        >
          <v-icon :icon="item.icon" size="18" />
          <span>{{ item.label }}</span>
        </router-link>
      </nav>
    </header>

    <main class="shell__main">
      <router-view />
    </main>

    <InviteCodesDialog ref="inviteDialog" />

    <v-dialog v-model="adminsDialog" max-width="520">
      <v-card rounded="lg" class="pa-5">
        <h3 class="text-body-1 font-weight-bold mb-2">管理员设置</h3>
        <p class="text-body-2 text-medium-emphasis mb-4">
          只有所有者能改这里。全板管理员名单：蔡松洋（所有者）、马霄宇（管理员）。普通用户不在名单里，靠这个名单区分。
        </p>
        <v-list density="compact">
          <v-list-item v-for="p in [SPACE.owner, ...SPACE.admins]" :key="p.handle" :title="p.name">
            <template #subtitle>{{ p.handle === SPACE.owner.handle ? '所有者' : '管理员' }}</template>
          </v-list-item>
        </v-list>
        <div class="text-right">
          <v-btn variant="text" @click="adminsDialog = false">关闭</v-btn>
        </div>
      </v-card>
    </v-dialog>
  </div>
</template>

<style scoped lang="scss">
.shell {
  display: flex;
  flex-direction: column;
  min-height: 100%;
  background: rgb(var(--v-theme-background));
}

.demo {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
  padding: 8px 20px;
  background: rgba(var(--v-theme-on-surface), 0.04);
  border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.07);
}

.demo__tag {
  padding: 2px 8px;
  color: rgb(var(--v-theme-on-surface));
  font-size: 0.7rem;
  background: rgba(var(--v-theme-primary), 0.16);
  border-radius: 999px;
}

.demo__text,
.demo__blurb {
  color: rgba(var(--v-theme-on-surface), 0.6);
  font-size: 0.76rem;
}

.demo__blurb {
  margin-left: auto;
  color: rgba(var(--v-theme-on-surface), 0.45);
}

.demo__btn {
  text-transform: none;
}

.demo__role {
  margin-left: 6px;
  color: rgba(var(--v-theme-on-surface), 0.5);
  font-size: 0.7rem;
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

.menu__sub {
  font-family: ui-monospace, 'SF Mono', Menlo, monospace;
  font-size: 0.72rem;
  letter-spacing: 0.04em;
}
</style>
