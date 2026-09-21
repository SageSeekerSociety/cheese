<script setup lang="ts">
import { onMounted } from 'vue'

import { useFeedbackStore } from '@/stores/feedback'

// 管理后台的外壳（`/admin/*`）。左边是分区，右边是当前那一块。
//
// 需求说的是「类似 admin.okcheese.com 的独立后台」：一个后台里有好几块，反馈管理
// 只是先有的那一块，成员管理是第二块，以后还会有别的。所以「反馈管理」不是一整页
// ——它是一页，字号、口径和用户侧的反馈中心不一样，理由写在 `AdminFeedbackPage`
// 顶部。这一层壳管的是**壳的事**：我能不能进来、左边有哪几块。
//
// 「我是不是管理员」由**服务端**答（`GET /feedback/meta` 的 `is_admin`，判据是
// `AdminService` 那份名单），三个状态在**这里画一次**：
//
// - meta 还在路上 → 「正在确认权限…」。少了这一档，一个真管理员打开页面看到的第一
//   句话是「你的账号不在管理员名单里」—— 一句假话，比一张空表更难查。
// - 不是管理员 → 一句人话加回去的路。**不是白屏、不是 404、不是一个空列表**：这三种
//   在界面上长得像「后台里没东西」，而真相是「你没在名单里」。
// - 是管理员 → 分区 + `<RouterView>` 画子页。
//
// 于是子页**不需要**再问一次「我是不是管理员」。判据写两份的症状是「反馈管理进得去、
// 成员管理进不去」，而两边各自看都「对」；更要紧的是子页只要被 `RouterView` 画出来，
// 就一定过了这道门 —— 「页面画了但没鉴权」这种状态在这里构造不出来。各块自己的接口
// 后面还会各自判一次（服务端不信客户端），那是服务端的事，不是这里省掉的。
//
// 左边哪几项是**写死的**，不从路由表算：路由表里还有 `/feedback/*` 三条用户侧的页，
// 按路由表算会把它们画进后台的导航里。
defineOptions({ name: 'AdminLayout' })

const store = useFeedbackStore()

/** 左边分区。加一块 = 加一行；链接指向的是路由名，改路径不用动这里。 */
const SECTIONS = [
  { to: '/admin/feedback', icon: 'mdi-tray-full', label: '反馈管理' },
  { to: '/admin/members', icon: 'mdi-account-multiple-outline', label: '成员管理' },
]

onMounted(() => {
  // meta 是这一层壳画哪一档的依据。它可能已经被用户侧拉过了，再调一次是幂等的 ——
  // 而直接输地址进来的时候没有它就没法判断。
  void store.loadMeta()
})
</script>

<template>
  <div class="admin-shell fill-height">
    <div v-if="!store.metaChecked" class="admin-shell__gate page-container">
      <v-icon size="28" class="mb-2">mdi-shield-account-outline</v-icon>
      <div class="t-body mb-1">正在确认权限…</div>
    </div>

    <div v-else-if="!store.isAdmin" class="admin-shell__gate page-container">
      <v-icon size="28" class="mb-2">mdi-shield-account-outline</v-icon>
      <div class="t-body mb-1">这一页是管理员后台</div>
      <div class="t-meta mb-3">你的账号不在管理员名单里，看不到这里的反馈 —— 私密反馈和安全问题对非管理员不存在</div>
      <v-btn variant="text" color="secondary" size="small" to="/feedback">回到反馈中心</v-btn>
    </div>

    <template v-else>
      <!-- 左边这一列自己滚：以后分区多了，它不该把右边的内容挤出窗口。 -->
      <nav class="admin-shell__rail" aria-label="管理后台分区">
        <div class="admin-shell__brand">
          <div class="t-eyebrow">管理后台</div>
        </div>
        <v-list density="compact" nav class="admin-shell__nav">
          <v-list-item
            v-for="section in SECTIONS"
            :key="section.to"
            :to="section.to"
            :prepend-icon="section.icon"
            :title="section.label"
            rounded="lg"
          />
        </v-list>
      </nav>

      <!-- 滚动归每一页自己领（仓库约定，见 styles/common.scss）：这一层只负责把宽度
           让出来，`min-width: 0` 是为了子页里的长表格能自己横向滚，而不是把整行撑破。 -->
      <main class="admin-shell__main">
        <RouterView />
      </main>
    </template>
  </div>
</template>

<style scoped>
.admin-shell {
  display: flex;
  min-height: 0;
}

/* 门口那两态是居中一句话，不是一页内容。 */
.admin-shell__gate {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  flex: 1 1 auto;
  padding-top: 48px;
}

.admin-shell__rail {
  flex: 0 0 auto;
  width: 196px;
  padding: 20px 8px 16px;
  border-right: 1px solid var(--line);
  overflow-y: auto;
}

.admin-shell__brand {
  padding: 0 12px 8px;
}

.admin-shell__main {
  flex: 1 1 auto;
  min-width: 0;
}
</style>
