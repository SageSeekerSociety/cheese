<script setup lang="ts">
import { onMounted } from 'vue'

import { useFeedbackStore } from '@/stores/feedback'

// 管理后台的外壳（`/admin/*`）。上面是一条分区栏，下面是当前那一块。
//
// 需求说的是「类似 admin.okcheese.com 的独立后台」：一个后台里有好几块，反馈管理
// 只是先有的那一块，成员管理是第二块，以后还会有别的。所以「反馈管理」不是一整页
// ——它是一页，字号、口径和用户侧的反馈中心不一样，理由写在 `AdminFeedbackPage`
// 顶部。这一层壳管的是**壳的事**：我能不能进来、上面有哪几块。
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
// 上面哪几项是**写死的**，不从路由表算：路由表里还有 `/feedback/*` 三条用户侧的页，
// 按路由表算会把它们画进后台的导航里。
//
// **分区横排，不是侧栏。**（这一版改的，理由按数字说。）上一版是一根 196px 的左栏。
// 它在 1440 宽下吃掉 13.6% 的宽度，却只装两项，而且**对「一屏能看到几行」毫无贡献**
// ——行是横向排的，侧栏占的是宽度，不是高度。收成一条 48px 的顶栏之后，那 196px 全部
// 回到内容列（最长的那一列因此从 ~440px 变成 ~492px），代价只有 48px 高，约 1.3 行。
// 根子上是**心智错了**：外壳按「导航树」画的，而这里只有 2 个平级目的地；平级目的地
// 就该横排，侧栏是留给有层级的那种。
defineOptions({ name: 'AdminLayout' })

const store = useFeedbackStore()

/** 上面那几块。加一块 = 加一行；链接指向的是路由名，改路径不用动这里。
 *
 *  **顶栏永远一行、永远 48px。** 到 5 项以上时多出来的要折进一个 `⋯` 菜单，
 *  而不是换行——换行会拿高度去换导航，那是这个改动的反面。现在只有 2 项，菜单
 *  还没有东西可装（写了也测不了），所以先留着这条规矩：**下一块进来时补菜单**，
 *  别改成 `flex-wrap: wrap`。 */
const SECTIONS = [
  { to: '/admin/feedback', icon: 'mdi-tray-full', label: '反馈管理' },
  { to: '/admin/members', icon: 'mdi-account-multiple-outline', label: '成员管理' },
  { to: '/admin/spaces', icon: 'mdi-check-decagram-outline', label: '题目板审核' },
]

onMounted(() => {
  // meta 是这一层壳画哪一档的依据。它可能已经被用户侧拉过了，再调一次是幂等的 ——
  // 而直接输地址进来的时候没有它就没法判断。
  void store.loadMeta()
})
</script>

<template>
  <div class="admin-shell">
    <header class="admin-shell__bar">
      <div class="admin-shell__brand t-eyebrow">管理后台</div>

      <!-- 分区。选中态是**中性**的（`--fill` 底 + `--ink` 字），不是琥珀：琥珀在这套
           规范里一屏只给一个主操作，而「我现在在哪一块」是位置，不是动作（唯一的
           例外是选项卡下划线那一种导航指示，见 AdminFeedbackPage）。 -->
      <nav v-if="store.isAdmin" class="admin-shell__sections" aria-label="管理后台分区">
        <RouterLink
          v-for="section in SECTIONS"
          :key="section.to"
          :to="section.to"
          class="admin-shell__pill"
          active-class="admin-shell__pill--on"
        >
          <v-icon :icon="section.icon" size="16" aria-hidden="true" />
          <span class="admin-shell__pill-label">{{ section.label }}</span>
        </RouterLink>
      </nav>
      <div v-else class="admin-shell__sections" />

      <!-- 回用户侧的路。它原来在「反馈管理」页头右上角，孤零零地飘着 —— 位置本身
           就是错的：这是一个「离开操作台」的动作，属于壳，而且成员管理页也需要它。 -->
      <RouterLink to="/feedback" class="admin-shell__leave">
        <v-icon icon="mdi-arrow-left" size="14" aria-hidden="true" />
        回到反馈中心
      </RouterLink>
    </header>

    <div v-if="!store.metaChecked" class="admin-shell__gate">
      <div class="admin-shell__gate-inner">
        <v-icon size="28" class="mb-2">mdi-shield-account-outline</v-icon>
        <div class="t-body mb-1">正在确认权限…</div>
      </div>
    </div>

    <div v-else-if="!store.isAdmin" class="admin-shell__gate">
      <div class="admin-shell__gate-inner">
        <v-icon size="28" class="mb-2">mdi-shield-account-outline</v-icon>
        <div class="t-body mb-1">这一页是管理员后台</div>
        <!-- 这句话以前还拖着一截「—— 私密反馈和安全问题对非管理员不存在」的规则说明。
             删掉它不是因为写错，是因为它不是读这句话的人要的东西：他刚被挡在门外，
             要知道的是「我为什么进不去」和「那我去哪」，不是这条规则的适用范围。
             还有一处更硬的：那一截挤在 `t-meta`（12.5px 等宽）那一档上，而它是一句
             正常的句子 —— 一句话不该坐在元信息的刻度上。现在整句是 `t-body`。 -->
        <div class="t-body mb-3">你的账号不在平台管理员名单里，无法访问管理后台。</div>
        <!-- 这一屏只有这一个动作，所以它是 `primary`。琥珀的规矩是「一屏只有一个主
             操作」，不是「管理员页面不许用琥珀」；这里没有第二个候选来稀释它。 -->
        <v-btn variant="text" color="primary" size="small" to="/feedback">回到反馈中心</v-btn>
      </div>
    </div>

    <!-- 滚动归每一页自己领：这一层只把高度和宽度让出来，`min-width: 0` 是为了子页
         里的宽表格能自己横向滚，而不是把整行撑破。 -->
    <main v-else class="admin-shell__main">
      <RouterView />
    </main>
  </div>
</template>

<style scoped>
.admin-shell {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}

.admin-shell__bar {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  height: 48px;
  padding: 0 24px;
  background: var(--surface);
  border-bottom: 1px solid var(--line);
}

/* 定宽标签位，而不是让分区跟着「管理后台」四个字走：这样几块分区的起始 x 在任何
   字号下都一样，将来换文案也不会整排挪位。 */
.admin-shell__brand {
  flex: 0 0 auto;
  min-width: 88px;
}

.admin-shell__sections {
  display: flex;
  flex: 1 1 auto;
  flex-wrap: nowrap;
  align-items: center;
  gap: 4px;
  min-width: 0;
}

.admin-shell__pill {
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 6px;
  height: 32px;
  padding: 0 12px;
  border-radius: var(--radius-md);
  color: var(--muted);
  font-size: 13px;
  font-weight: 600;
  line-height: var(--lh-13);
  text-decoration: none;
  white-space: nowrap;
  transition:
    background-color 0.12s ease,
    color 0.12s ease;
}

.admin-shell__pill:hover {
  background: var(--fill);
  color: var(--text);
}

.admin-shell__pill--on,
.admin-shell__pill--on:hover {
  background: var(--fill);
  color: var(--ink);
}

/* 图标只在窄屏顶上：宽屏时标签就够认，加一个图标只是多一层噪声；窄屏收起标签之后
   图标是唯一还看得见的东西，所以那时它必须出现，并且由 `aria-label` 和 `title`
   保住可读的名字。 */
.admin-shell__pill .v-icon {
  display: none;
}

.admin-shell__leave {
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 4px;
  height: 32px;
  margin-left: 12px;
  padding: 0 8px;
  border-radius: var(--radius-md);
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
  text-decoration: none;
  white-space: nowrap;
  transition:
    background-color 0.12s ease,
    color 0.12s ease;
}

.admin-shell__leave:hover {
  background: var(--fill);
  color: var(--text);
}

@media (max-width: 1080px) {
  .admin-shell__bar {
    padding: 0 16px;
  }

  .admin-shell__brand {
    min-width: 0;
    margin-right: 16px;
  }

  .admin-shell__pill .v-icon {
    display: inline-flex;
  }

  .admin-shell__pill-label {
    display: none;
  }
}

/* 门口那两态是居中一句话，不是一页内容。 */
.admin-shell__gate {
  display: flex;
  flex: 1 1 auto;
  align-items: center;
  justify-content: center;
  min-height: 0;
  padding: 48px 24px;
}

.admin-shell__gate-inner {
  display: flex;
  flex-direction: column;
  align-items: center;
  max-width: 420px;
  text-align: center;
}

.admin-shell__main {
  flex: 1 1 auto;
  min-width: 0;
  min-height: 0;
}
</style>
