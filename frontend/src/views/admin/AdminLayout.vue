<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { useMediaQuery } from '@vueuse/core'

import AdminShortcutSheet from '@/components/admin/AdminShortcutSheet.vue'
import { useFeedbackStore } from '@/stores/feedback'

// 管理后台的外壳（`/admin/*`）。左边一条分区栏，右边装当前那一块。
//
// 需求说的是「类似 admin.okcheese.com 的独立后台」：一个后台里有好几块，反馈只是先有
// 的那一块，成员是第二块，看板是第三块。所以「反馈」不是一整页 —— 它是一页，字号、口径
// 和用户侧的反馈中心不一样。这一层壳管的是**壳的事**：我能不能进来、旁边有哪几块、全局
// 那几颗键归谁。
//
// **侧栏回到 200px，是这一版改的，理由按数字说。** 上一版把导航收成 48px 的横条，为的是
// 把 196px 宽度还给内容列 —— 那个算式没错，但它算的是「内容能宽多少」，而这一版的队列
// 根本不吃满宽度：内容列锁死 1100px（§4.1），1440 视口下横竖都多出 140px。也就是说那
// 196px 还回去也没人用，而横条把三件事挤在一条线上：分区、未读徽标、回工作区的路。
// 分区到第三块之后，横排要么折行（拿高度换导航）要么进 `⋯` 菜单（多一次点击），两条都
// 不如一条竖栏。竖栏还顺带给了「未读 12」一个不用跟标题抢位置的地方。
//
// 「我是不是管理员」由**服务端**答（`GET /feedback/meta` 的 `is_admin`，判据是
// `AdminService` 那份名单），三个状态在**这里画一次**：
//
// - meta 还在路上 → 「正在确认权限…」。少了这一档，一个真管理员打开页面看到的第一句话
//   是「你的账号不在管理员名单里」—— 一句假话，比一张空表更难查。
// - 不是管理员 → 一句人话加回去的路。**不是白屏、不是 404、不是一个空列表**：这三种在
//   界面上长得像「后台里没东西」，而真相是「你没在名单里」。
// - 是管理员 → 分区 + `<RouterView>` 画子页。
//
// 于是子页**不需要**再问一次「我是不是管理员」。判据写两份的症状是「反馈进得去、成员
// 进不去」，而两边各自看都「对」；更要紧的是子页只要被 `RouterView` 画出来，就一定过了
// 这道门 —— 「页面画了但没鉴权」这种状态在这里构造不出来。各块自己的接口后面还会各自判
// 一次（服务端不信客户端），那是服务端的事，不是这里省掉的。
//
// 上面哪几项是**写死的**，不从路由表算：路由表里还有 `/feedback/*` 三条用户侧的页，按
// 路由表算会把它们画进后台的导航里。
defineOptions({ name: 'AdminLayout' })

const store = useFeedbackStore()
const route = useRoute()
const router = useRouter()
const { t } = useI18n()

/** 旁边那几块。加一块 = 加一行。`badge` 是「这一项旁边挂一个数」的意思，今天只有队列
 *  有（未读），但挂在数据里而不是写死在模板上 —— 下一块要挂数时不用改模板。
 *
 *  `label` 写成函数而不是串（照 `AdminQueuePage` 的 `LANE_LABEL` 模式）：`t()` 要在读的
 *  那一刻取当前语言，写成常量只会在 setup 时求值一次，切了语言侧栏不跟。键**逐字写全、
 *  不拼** —— i18n 闸门（`src/i18n/catalog.spec.ts`）照源码字面量认「这个键有人用」。 */
const SECTIONS: { to: string; name: string; icon: string; label: () => string; badge: boolean }[] = [
  {
    to: '/admin/queue',
    name: 'AdminQueue',
    icon: 'mdi-tray-full',
    label: () => t('navigation.admin.queue'),
    badge: true,
  },
  {
    to: '/admin/dashboard',
    name: 'AdminDashboard',
    icon: 'mdi-chart-line',
    label: () => t('navigation.admin.dashboard'),
    badge: false,
  },
  // 「模型」放在看板后面：它和看板看的是同一条链（网关上的模型与它们花掉的钱），
  // 只是看板报量、这一块管钱和上架。入口名说的是里面装的是什么。
  {
    to: '/admin/models',
    name: 'AdminModels',
    icon: 'mdi-cube-outline',
    label: () => t('navigation.admin.models'),
    badge: false,
  },
  // 「开板申请」而不是「题目板审核」：需求方在这一页上问过「题目板审核是什么」——
  // 名字说的是**你对它做什么**（审核），而他要找的是**这里面装的是什么**（有人申请开
  // 一个新题目板）。入口的名字该回答后者，动作（批准 / 驳回）是页面里的事。
  {
    to: '/admin/spaces',
    name: 'AdminSpaces',
    icon: 'mdi-check-decagram-outline',
    label: () => t('navigation.admin.spaces'),
    badge: false,
  },
  {
    to: '/admin/members',
    name: 'AdminMembers',
    icon: 'mdi-account-multiple-outline',
    label: () => t('navigation.admin.members'),
    badge: false,
  },
]

/** 折叠成 56px（§10.2）。**不落盘**：壳在同一个会话里不重新挂载，切分区不会把它弹回来，
 *  而下次进来回到展开态是更常见的那种期望（这一条没有实测依据，是取舍）。 */
const collapsed = ref(false)

/** 窄屏（手机）默认收起。200px 的侧栏在 390px 的屏上吃掉一半宽度，页面里那句页头会
 *  被挤成一个字一行、表格只剩一条缝 —— 实测 /admin/members 与 /admin/models 都是
 *  这样。**不是「用户偏好」而是「这一栏放不下」**，所以它跟着视口走：进窄屏自动收起
 *  （只剩图标，§10.2 那个形态），回到宽屏自动展开。手动那颗开关照旧，用户在这之后
 *  的选择不会被下一次 resize 之前的任何东西覆盖。 */
const narrow = useMediaQuery('(max-width: 700px)')
watch(narrow, (isNarrow) => (collapsed.value = isNarrow), { immediate: true })

/** `?` 那一层（§8）。480px，`Esc` 关闭由 Vuetify 的对话框自己管。 */
const shortcutOpen = ref(false)

const unread = computed(() => store.counts.unread ?? 0)

/** `G` 之后那一颗（§8 的序列键）。1s 内有效，超时就算没按过 —— 不然「按了 G 去泡咖啡、
 *  回来顺手按了个 D」会把人送去看板。 */
let gPressedAt = 0
const SEQUENCE_MS = 1000

function isTyping(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null
  if (!el || !el.tagName) return false
  return el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT' || el.isContentEditable
}

/** 当前在哪一条路由上。**用它而不是读 DOM 上的 `aria-current`**：徽标和折叠态也要这个
 *  判断，而 `aria-current` 是 vue-router 画上去的，读回来要过一遍元素。
 *  `aria-current="page"` 本身由 `RouterLink` 在精确命中时自己写（`ariaCurrentValue`
 *  默认就是 `page`），不用手写 —— 手写一份两份判断迟早会不一致。 */
function isCurrent(name: string): boolean {
  // `/admin/feedback` 是队列的旧地址（薄壳），导航项落在 `/admin/queue` 上，所以那一条
  //  路由也要算「在队列这一块里」—— 否则从老书签进来时旁边一条都不亮。
  if (name === 'AdminQueue') return route.name === 'AdminQueue' || route.name === 'AdminFeedback'
  return route.name === name
}

function refreshCurrent() {
  // `R` 是全局键（§8），而「当前这一页该重拉什么」只有路由知道。这里按路由名分派，
  // 而不是让每一页各自去监听一个自定义事件：后者要三处各自挂一次监听、三处各自记得
  // 解绑，而这里的分支只有两行。
  if (route.name === 'AdminDashboard') void store.loadStats()
  else if (route.name === 'AdminQueue' || route.name === 'AdminFeedback') void store.loadAdmin()
}

function onKeydown(event: KeyboardEvent) {
  // 带修饰键的一律放行：那是浏览器/系统的快捷键（`Cmd+F`、`Ctrl+K`），这一层不该抢。
  if (event.metaKey || event.ctrlKey || event.altKey) return
  if (isTyping(event.target)) return

  if (event.key === '?') {
    event.preventDefault()
    shortcutOpen.value = true
    return
  }
  if (event.key === 'r' || event.key === 'R') {
    event.preventDefault()
    refreshCurrent()
    return
  }

  if (event.key === 'g' || event.key === 'G') {
    gPressedAt = Date.now()
    return
  }

  // 序列键的第二颗。不在窗口里就当作普通按键放走 —— 不能 `preventDefault`，不然在
  //  没按 `G` 的时候按 `q` 会变成一个什么都不做的黑洞。
  if (Date.now() - gPressedAt >= SEQUENCE_MS) return
  gPressedAt = 0
  const key = event.key.toLowerCase()
  if (key === 'q') {
    event.preventDefault()
    void router.push('/admin/queue')
  } else if (key === 'd') {
    event.preventDefault()
    void router.push('/admin/dashboard')
  } else if (key === 'f') {
    event.preventDefault()
    void router.push('/feedback')
  }
}

onMounted(() => {
  // meta 是这一层壳画哪一档的依据。它可能已经被用户侧拉过了，再调一次是幂等的 ——
  // 而直接输地址进来的时候没有它就没法判断。
  void store.loadMeta().then(() => {
    // 未读徽标挂在导航上，而它旁边的子页不一定是队列（成员页不拉 counts）。所以这一层
    // 自己问一次 —— 少了它，在成员页上那个数永远停在 0，看着像「没有未读」。
    if (store.isAdmin) void store.refreshCounts()
  })
  window.addEventListener('keydown', onKeydown)
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeydown)
})
</script>

<template>
  <div class="admin-shell" :class="{ 'admin-shell--collapsed': collapsed }">
    <aside class="admin-shell__nav">
      <div class="admin-shell__brand t-eyebrow">芝士 · 管理</div>

      <nav v-if="store.isAdmin" class="admin-shell__items" aria-label="管理后台分区">
        <!-- 选中态是**中性**的（`--fill` 底 + `--ink` 字）加上左边那道 2px 的 `--accent`
             竖条 —— 琥珀在这一套规范里只当填充色（§7.3：对比度 2.65:1，当不了线色），
             而导航这一处是它在全站唯一的例外（§7.4 的「既有的导航豁免」，一屏一条）。 -->
        <RouterLink
          v-for="section in SECTIONS"
          :key="section.to"
          :to="section.to"
          class="admin-shell__item"
          :class="{ 'admin-shell__item--on': isCurrent(section.name) }"
          :title="section.label()"
        >
          <v-icon :icon="section.icon" size="18" aria-hidden="true" />
          <span class="admin-shell__label">{{ section.label() }}</span>
          <span
            v-if="section.badge && unread > 0"
            class="admin-shell__badge t-num"
            :aria-label="t('feedback.dashboard.kpi.unread')"
            :title="t('feedback.dashboard.kpi.unread')"
          >
            {{ unread }}
          </span>
        </RouterLink>
      </nav>

      <div class="admin-shell__spacer" />

      <!-- 回用户侧的路。它原来在「反馈」页的页头右上角，孤零零地飘着 —— 位置本身就是
           错的：这是一个「离开操作台」的动作，属于壳，而且成员页也需要它。 -->
      <RouterLink to="/feedback" class="admin-shell__leave" title="返回工作区">
        <v-icon icon="mdi-arrow-left" size="16" aria-hidden="true" />
        <span class="admin-shell__label">返回工作区</span>
      </RouterLink>

      <!-- 折叠。放在最下面：它是「这一栏怎么显示」的开关，不是目的地之一，和上面那三个
           平铺在一起会变成第四个分区。 -->
      <button
        type="button"
        class="admin-shell__collapse"
        :aria-expanded="!collapsed"
        aria-label="折叠导航"
        @click="collapsed = !collapsed"
      >
        <v-icon :icon="collapsed ? 'mdi-chevron-right' : 'mdi-chevron-left'" size="16" aria-hidden="true" />
        <span class="admin-shell__label">收起</span>
      </button>
    </aside>

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

    <AdminShortcutSheet v-model="shortcutOpen" />
  </div>
</template>

<style scoped>
.admin-shell {
  display: flex;
  height: 100%;
  min-height: 0;
}

.admin-shell__nav {
  display: flex;
  flex: 0 0 200px;
  flex-direction: column;
  box-sizing: border-box;
  width: 200px;
  min-height: 0;
  padding: 0 8px 8px;
  background: var(--surface);
  border-right: 1px solid var(--line);
  transition: width 0.2s ease;
}

.admin-shell--collapsed .admin-shell__nav {
  flex-basis: 56px;
  width: 56px;
}

.admin-shell__brand {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  height: 56px;
  padding: 0 12px;
  color: var(--muted);
  white-space: nowrap;
}

.admin-shell--collapsed .admin-shell__brand {
  justify-content: center;
  padding: 0;
  font-size: 12px;
}

.admin-shell__items {
  display: flex;
  flex: 0 0 auto;
  flex-direction: column;
  gap: 4px;
}

/* 一行的几何：左内边距 10 + 图标 18 + 缝 10 + 标签。选中那条的 2px 条压在左内边距里
   （`::before` 绝对定位，不占位），所以文字在任何一条上都不平移。 */
.admin-shell__item {
  position: relative;
  display: flex;
  align-items: center;
  gap: 8px;
  box-sizing: border-box;
  height: 36px;
  padding: 0 8px;
  border-top-left-radius: var(--radius-md);
  border-top-right-radius: var(--radius-md);
  border-bottom-right-radius: var(--radius-md);
  border-bottom-left-radius: var(--radius-md);
  color: var(--muted);
  font-size: 14px;
  font-weight: 600;
  line-height: var(--lh-14);
  text-decoration: none;
  white-space: nowrap;
  transition:
    background-color 0.12s ease,
    color 0.12s ease;
}

.admin-shell__item:hover {
  background: var(--fill);
  color: var(--text);
}

.admin-shell__item--on,
.admin-shell__item--on:hover {
  background: var(--fill);
  color: var(--ink);
}

.admin-shell__item--on::before {
  content: '';
  position: absolute;
  top: 8px;
  bottom: 8px;
  left: 0;
  width: 2px;
  background: var(--accent);
  border-top-left-radius: var(--radius-sm);
  border-top-right-radius: var(--radius-sm);
  border-bottom-right-radius: var(--radius-sm);
  border-bottom-left-radius: var(--radius-sm);
}

.admin-shell__label {
  flex: 1 1 auto;
  overflow: hidden;
  min-width: 0;
  text-overflow: ellipsis;
}

.admin-shell__badge {
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  height: 18px;
  padding: 0 4px;
  background: var(--fill-2);
  border-top-left-radius: var(--radius-sm);
  border-top-right-radius: var(--radius-sm);
  border-bottom-right-radius: var(--radius-sm);
  border-bottom-left-radius: var(--radius-sm);
  color: var(--ink);
  font-size: 12px;
  font-weight: 600;
  line-height: var(--lh-12);
}

.admin-shell__spacer {
  flex: 1 1 auto;
  min-height: 0;
}

.admin-shell__leave,
.admin-shell__collapse {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 8px;
  box-sizing: border-box;
  height: 32px;
  padding: 0 8px;
  background: transparent;
  border: 0;
  border-top-left-radius: var(--radius-md);
  border-top-right-radius: var(--radius-md);
  border-bottom-right-radius: var(--radius-md);
  border-bottom-left-radius: var(--radius-md);
  color: var(--muted);
  font-size: 13px;
  font-weight: 600;
  line-height: var(--lh-13);
  text-align: left;
  white-space: nowrap;
  cursor: pointer;
  transition:
    background-color 0.12s ease,
    color 0.12s ease;
}

.admin-shell__leave {
  text-decoration: none;
}

.admin-shell__leave:hover,
.admin-shell__collapse:hover {
  background: var(--fill);
  color: var(--text);
}

/* 折叠态：只剩图标。标签收起来而不是换个布局 —— 图标在两种状态下都在同一个 x 上，
   眼睛不用重新找位置。 */
.admin-shell--collapsed .admin-shell__label,
.admin-shell--collapsed .admin-shell__badge {
  display: none;
}

.admin-shell--collapsed .admin-shell__item,
.admin-shell--collapsed .admin-shell__leave,
.admin-shell--collapsed .admin-shell__collapse {
  justify-content: center;
  padding: 0;
}

.admin-shell--collapsed .admin-shell__item--on::before {
  top: 4px;
  bottom: 4px;
}

/* 门口那两态是居中一句话，不是一页内容。 */
.admin-shell__gate {
  display: flex;
  flex: 1 1 auto;
  align-items: center;
  justify-content: center;
  min-width: 0;
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
