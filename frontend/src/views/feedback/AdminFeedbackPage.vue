<script setup lang="ts">
import type { AdminSort, AdminTab } from '@/stores/feedback'

import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import AdminGrid from '@/components/admin/AdminGrid.vue'
import AdminStatusCell from '@/components/admin/AdminStatusCell.vue'
import AdminFeedbackDetailDrawer from '@/components/feedback/AdminFeedbackDetailDrawer.vue'
import FeedbackAuthorAvatar from '@/components/feedback/FeedbackAuthorAvatar.vue'
import { KIND_LABEL, priorityMeta, SOURCE_LABEL } from '@/lib/feedbackMeta'
import { relTime } from '@/lib/relTime'
import { useFeedbackStore } from '@/stores/feedback'

// 管理员后台的「反馈管理」（/admin/feedback）。
//
// 需求说它属于**独立的**后台（类似 admin.okcheese.com），不放在用户侧反馈中心里。
// 所以这一页有自己的外壳和口径，和 /feedback 长得不一样是**故意的**：两边面对的
// 问题不同 —— 用户侧问「有没有人也遇到这个」，管理侧问「这一条现在该谁动」。同一个
// 界面同时回答这两个问题，结果是两边都不好用。
//
// 这一版把「管理侧问的那个问题」当成**唯一**的设计目标（三个独立评审都指出上一版
// 没回答它）。落下来是三件事：
//
//   1. **一屏看完全部待办**。行从「57px 两行卡」压成「36px 单行」，列从 7 列加到 11
//      列 —— 一屏 17 行 × 11 列。第二行里那两条信息各归各位：`display_id` 变成第 1
//      列、`@assignee` 变成第 10 列。信息没丢，只是从「堆在一个格子里」变成「各占一
//      列、可以竖着扫」。
//   2. **「这一条该谁动」一眼看得出来**：状态是四格梯子（不是一个小圆点）、支持数和
//      评论数各自一列、指派和优先级各自一列，最后活动时间在右端。
//   3. **手不离键盘**。整行可点，但 Tab 只停一次（停在标题按钮上，见下面的 `::after`）；
//      `/` 跳到搜索、`j`/`k` 或上下键在行之间走、`回车`开抽屉、`Esc` 关。
//
// 「我是不是管理员」**不在这里问**：外壳（`AdminLayout`）已经问过并且把整块后台挡在
// 门后了，这一页能画出来就说明这个人过了那道门。那份三段门（还没问完 / 不是管理员 /
// 是）连同它踩过的坑一起搬到了 `views/admin/AdminLayout.spec.ts`。
defineOptions({ name: 'AdminFeedbackPage' })

const store = useFeedbackStore()

const TABS: { value: AdminTab; label: string }[] = [
  { value: 'public', label: '公开反馈' },
  { value: 'private', label: '私密反馈' },
  { value: 'agent', label: 'Agent 发现' },
  { value: 'security', label: '安全问题' },
]
// 管理端四栏**不显示计数**：服务端的 counts 是给用户侧那四栏（全部/热门/处理中/
// 已完成）算的，口径不同 —— 拿它填这几栏是编数字。

/** 排序。服务端的 `SORTS` 就是这两个（`?sort=`）；不认识的值服务端回 400，不是
 *  悄悄退回 `new` —— 排序就是这一页的答案，答成另一种排序等于用同一个标题回答了
 *  另一个问题。 */
const SORT_OPTIONS: { value: AdminSort; label: string; hint: string }[] = [
  { value: 'new', label: '最新', hint: '按提交时间，新提的在前' },
  { value: 'supports', label: '最热', hint: '按支持数从多到少 —— 注意它不带时间衰减，上周爆的和昨天爆的同权' },
]

/** 11 列。**只给标题列 `null`**：`table-layout: fixed` 下没有宽度的列吃掉剩下的全
 *  部，所以哪一列是「自适应列」必须唯一，否则剩下的宽度会被平摊、密度就散了。
 *
 *  固定列合计 916px，1440 宽的窗口里内容区是 1392，标题因此拿到 476。
 *
 *  用户列是 184 而不是更小的那几档，算式在这里：格内可用 = 184 − 12×2 = 160；
 *  单元内容是头像 20 + 6 + handle（15 个字符，13px 下约 7.1px/字符 ≈ 107）+ 6 +
 *  私密锁图标 12 = **151**，余 9px。写成 168 时可用只有 144，真实数据下 handle 会被
 *  吃掉最后一个字符 —— 那不算崩，但「刚好截在最后一个字符上」看着像坏了。 */
const COLS: (string | null)[] = [
  '76px', // ID
  null, // 标题（自适应）
  '184px', // 用户
  '92px', // 来源
  '56px', // 类型
  '72px', // 优先级
  '136px', // 状态
  '48px', // 支持
  '48px', // 评论
  '116px', // 指派
  '88px', // 更新
]

/** 骨架里每根骨头占该格宽度的比例，按列给 —— 和真数据同一份列序，所以骨架不会错位。 */
const BONE_WIDTHS = ['70%', '86%', '72%', '58%', '44%', '64%', '78%', '40%', '40%', '60%', '56%']

const selectedId = ref<string | null>(null)
const drawerOpen = ref(false)

const searchEl = ref<{ focus: () => void } | null>(null)
const gridEl = ref<InstanceType<typeof AdminGrid> | null>(null)

function open(id: string) {
  selectedId.value = id
  drawerOpen.value = true
}

// 关掉抽屉时把选中项一起清掉：留着它，下次点另一条之前会先闪一下上一条的内容。
function close(open: boolean) {
  drawerOpen.value = open
  if (!open) selectedId.value = null
}

/* ---- 表格的四种状态 ---- */

/** 首次加载。**只在手上一条都没有时给骨架** —— 已经有内容时换骨架会让整表闪一下，
 *  那是比「旧内容多停半秒」更糟的手感（那种情况给 `busy`，表体压暗一档）。 */
const showSkeleton = computed(() => store.adminLoading && !store.adminItems.length)

/** 下拉刷新中：内容还在，只是不新鲜了。 */
const busy = computed(() => store.adminLoading && store.adminItems.length > 0)

/** 表格自己的错误。**抽屉开着时不画**：那时错误来自抽屉里的写操作，画在这一页上会
 *  看着像「列表读失败了」。 */
const tableError = computed(() => (drawerOpen.value ? null : store.error))

/** 加载完了、这一栏一条都没有。搜索词非空时说的是另一句话 —— 「这一栏没有反馈」和
 *  「没有你要找的那条」是两件事，后者要说清是**筛掉了**而不是不存在。 */
const emptyText = computed<string | null>(() => {
  if (showSkeleton.value || store.adminItems.length) return null
  // 读失败时不说「暂无」：那是把一次网络故障说成「平台没有你的数据」，而这上面那条
  // alert 已经在说真正的原因了。
  if (tableError.value) return null
  return store.adminQuery.trim() ? '暂无匹配的反馈' : '暂无反馈'
})

/** 读失败且一条都没有时，整张表都不画（只画 alert）：一张只有表头、下面空着的表格
 *  看着像坏了，而它和「这一栏没有反馈」在屏幕上会长得一模一样。 */
const showTable = computed(() => !(tableError.value && !store.adminItems.length))

/* ---- 页头和状态条上的两个数 ---- */

/** 共多少条。**总数只出现在这一处**（设计稿里它同时出现在页头和状态条上，那是同一个
 *  数字写两遍；状态条改说「哪一段」）。 */
const countLine = computed(() => {
  if (showSkeleton.value) return ''
  const base = `共 ${store.adminTotal} 条`
  const q = store.adminQuery.trim()
  return q ? `${base} · 匹配「${q}」` : base
})

/** 手上这一段是第几条到第几条。只有一页时留空 —— 「第 1–3 条」和它上面那句「共 3 条」
 *  说的是同一件事。 */
const rangeLine = computed(() => {
  if (store.adminItems.length < 1 || !store.adminHasNext) return ''
  const from = store.adminPageStart + 1
  return `第 ${from}–${store.adminPageStart + store.adminItems.length} 条`
})

/* ---- 键盘 ---- */

/** 焦点是不是在一个正在打字的地方。`/` 和 `j`/`k` 这两种快捷键都必须先问过它 ——
 *  否则在搜索框里打 `/` 会跳走焦点、打 `j` 会跳到下一行。 */
function isTyping(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null
  if (!el || !el.tagName) return false
  return el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT' || el.isContentEditable
}

/** 在行之间挪焦点。**挪的是焦点，不是「选中项」**：焦点落在标题按钮上时，读屏念的是
 *  那条反馈的标题，`回车` 是浏览器原生的按钮激活 —— 不需要自己实现一套「选中」。 */
function moveFocus(step: number) {
  const host = gridEl.value?.$el as HTMLElement | undefined
  const links = Array.from(host?.querySelectorAll<HTMLElement>('tr.fb-row .fbrow__link') ?? [])
  if (!links.length) return
  const here = document.activeElement as HTMLElement | null
  const at = here ? links.indexOf(here) : -1
  if (at < 0) {
    links[step > 0 ? 0 : links.length - 1]?.focus()
    return
  }
  const next = Math.min(links.length - 1, Math.max(0, at + step))
  links[next]?.focus()
}

function onKeydown(event: KeyboardEvent) {
  // 带修饰键的一律放行：那是浏览器/系统的快捷键（`Cmd+F`、`Ctrl+K`），这一页不该抢。
  if (event.metaKey || event.ctrlKey || event.altKey) return

  if (isTyping(event.target)) {
    if (event.key === 'Escape') (event.target as HTMLElement).blur()
    return
  }

  if (event.key === '/') {
    event.preventDefault()
    searchEl.value?.focus()
    return
  }
  if (event.key === 'j' || event.key === 'ArrowDown') {
    event.preventDefault()
    moveFocus(1)
    return
  }
  if (event.key === 'k' || event.key === 'ArrowUp') {
    event.preventDefault()
    moveFocus(-1)
    return
  }
  if (event.key === 'Escape' && drawerOpen.value) close(false)
}

onMounted(() => {
  void store.loadAdmin()
  window.addEventListener('keydown', onKeydown)
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeydown)
})
</script>

<template>
  <div class="fbadmin">
    <header class="fbadmin__head">
      <h1 class="t-page-title">反馈管理</h1>
      <p class="fbadmin__sub t-meta">{{ countLine }}</p>
    </header>

    <div class="fbadmin__rails">
      <!-- 栏位是服务端的筛选（`?tab=`），不认的值服务端回 400 —— 所以这里切栏位
           就是重新拉一页，不做本地过滤（本地过滤是第二份实现，两边迟早不一样）。
           切栏位顺带把页码归零，那件事在 store 里（见 `setAdminTab`）。 -->
      <v-tabs
        :model-value="store.adminTab"
        density="compact"
        color="primary"
        class="fbadmin__tabs"
        @update:model-value="store.setAdminTab($event as AdminTab)"
      >
        <v-tab v-for="tab in TABS" :key="tab.value" :value="tab.value">{{ tab.label }}</v-tab>
      </v-tabs>

      <div class="fbadmin__tools">
        <v-text-field
          ref="searchEl"
          :model-value="store.adminQuery"
          class="fbadmin__search"
          variant="outlined"
          density="compact"
          hide-details
          placeholder="搜索标题 / FB-1042 / @handle"
          prepend-inner-icon="mdi-magnify"
          aria-label="搜索反馈"
          autocomplete="off"
          @update:model-value="store.setAdminQuery($event)"
        />
        <button v-if="store.adminQuery" type="button" class="fbadmin__linkbtn" @click="store.clearAdminQuery()">
          清除
        </button>

        <!-- 排序。两个平级取值，所以是一个分段控件而不是下拉框 —— 下拉框要多一次
             点击才看得见「另一个取值是什么」。 -->
        <div class="fbadmin__seg" role="group" aria-label="排序">
          <button
            v-for="option in SORT_OPTIONS"
            :key="option.value"
            type="button"
            class="fbadmin__seg-btn"
            :class="{ 'fbadmin__seg-btn--on': store.adminSort === option.value }"
            :aria-pressed="store.adminSort === option.value"
            :title="option.hint"
            @click="store.setAdminSort(option.value)"
          >
            {{ option.label }}
          </button>
        </div>

        <div class="fbadmin__spacer" />

        <v-btn
          icon="mdi-refresh"
          variant="text"
          size="small"
          aria-label="刷新"
          title="刷新（R）"
          :loading="store.adminLoading"
          @click="store.loadAdmin()"
        />
      </div>
    </div>

    <v-alert v-if="tableError" type="error" density="compact" variant="tonal" class="fbadmin__alert">
      {{ tableError }}
      <template #append>
        <v-btn variant="text" size="small" @click="store.loadAdmin()">重试</v-btn>
      </template>
    </v-alert>

    <AdminGrid
      v-if="showTable"
      ref="gridEl"
      class="fbadmin__grid"
      label="反馈列表"
      :cols="COLS"
      :bone-widths="BONE_WIDTHS"
      :loading="showSkeleton"
      :busy="busy"
      :empty="emptyText"
      :skeleton-rows="12"
    >
      <template #head>
        <tr>
          <th scope="col">ID</th>
          <th scope="col">标题</th>
          <th scope="col">用户</th>
          <th scope="col">来源</th>
          <th scope="col">类型</th>
          <th scope="col">优先级</th>
          <th scope="col">状态</th>
          <th scope="col" class="fbadmin__num">支持</th>
          <th scope="col" class="fbadmin__num">评论</th>
          <th scope="col">指派</th>
          <th scope="col" class="fbadmin__num">更新</th>
        </tr>
      </template>

      <tr v-for="item in store.adminItems" :key="item.id" class="fb-row fbadmin__row">
        <td class="fbadmin__cell fbadmin__id">{{ item.display_id }}</td>

        <td class="fbadmin__cell">
          <!-- 整行可点，但**只占一个 Tab 停靠点**：链接是一条真的 `<button>`
               （键盘够得着、读屏念得出名字、回车开抽屉），它的 `::after` 铺满整行，
               于是鼠标点在行里任何位置都等于点它。这就是 GitHub Issues 那一套。
               行上原来是 `@click` 的 `<tr>` —— 鼠标能用、Tab 整段跳过去，正是规范
               禁止的「不可聚焦的东西当按钮」。
               **两处代价，写在这里免得下一个人踩**：① 别的格里没法用鼠标划选文字
               （那一层盖在上面）；② 将来某一行要加自己的按钮时，那个按钮得写
               `position: relative; z-index: 2` 才点得到。今天的动作都在抽屉里。 -->
          <button type="button" class="fbrow__link" @click="open(item.id)">{{ item.title }}</button>
        </td>

        <td class="fbadmin__cell">
          <span class="fbadmin__user">
            <FeedbackAuthorAvatar
              :handle="item.author_handle"
              :is-agent="item.author_is_agent"
              :avatar-id="item.author_avatar_id"
              :size="20"
            />
            <span class="fbadmin__handle">{{ item.author_handle }}</span>
            <!-- 私密反馈在管理端必须看得出来：它和公开的挤在同一栏里长得一样，管理员
                 就得靠读正文才发现「这条别人看不到」。图标 + `title`，读屏不念它
                 （念了也只是重复 handle 后面一个符号的名字）。 -->
            <v-icon
              v-if="item.visibility === 'private'"
              icon="mdi-lock-outline"
              size="12"
              class="fbadmin__lock"
              aria-hidden="true"
              title="私密反馈：只有提交者本人和平台管理员看得到"
            />
          </span>
        </td>

        <td class="fbadmin__cell">
          <!-- 来源这一格和反馈卡、详情页用的是同一个形态（中性 chip + 机器人图标）：
               三处说的是同一件事，长得一样才不用重新认。琥珀留给这一页唯一的主操作
               ——这一页没有主操作，它的动作在每一行里。 -->
          <span v-if="item.author_is_agent" class="chip-neutral">
            <v-icon size="12">mdi-robot-outline</v-icon>{{ SOURCE_LABEL.agent }}
          </span>
          <span v-else class="fbadmin__dim">{{ SOURCE_LABEL.user }}</span>
        </td>

        <td class="fbadmin__cell fbadmin__dim">{{ KIND_LABEL[item.kind] }}</td>

        <td class="fbadmin__cell">
          <!-- 优先级**不铺底色**（上一版用的是 `FeedbackStatusChip`，带 wash 底）。
               一屏十七行、两列都带底的话整张表会变成一条条色块，wash 留给抽屉和卡片
               ——那里一屏只有一个。这是刻意的密度取舍，不是漏改。 -->
          <span class="fbadmin__pri">
            <span class="status-dot" :style="{ background: priorityMeta(item.priority).dot }" aria-hidden="true" />
            <span :style="{ color: priorityMeta(item.priority).ink }">{{ priorityMeta(item.priority).label }}</span>
          </span>
        </td>

        <td class="fbadmin__cell">
          <AdminStatusCell :status="item.status" />
        </td>

        <td class="fbadmin__cell fbadmin__num">{{ item.supports }}</td>
        <td class="fbadmin__cell fbadmin__num">{{ item.comments }}</td>

        <td class="fbadmin__cell">
          <span v-if="item.assignee_handle" class="fbadmin__handle">@{{ item.assignee_handle }}</span>
          <span v-else class="fbadmin__dim">未指派</span>
        </td>

        <td class="fbadmin__cell fbadmin__num fbadmin__when">
          {{ relTime(item.last_activity_at ?? item.created_at) }}
        </td>
      </tr>

      <template #foot>
        <span class="t-meta">{{ rangeLine }}</span>
        <span class="fbadmin__pager">
          <v-btn variant="outlined" size="small" :disabled="!store.adminHasPrev" @click="store.adminPrev()">
            上一页
          </v-btn>
          <v-btn variant="outlined" size="small" :disabled="!store.adminHasNext" @click="store.adminNext()">
            下一页
          </v-btn>
        </span>
      </template>
    </AdminGrid>

    <!-- 传 id 而不是整行：抽屉里的写操作（改状态、指派、标安全问题）服务端回的
         **是刷新后的整条详情**，本地那份行数据当场就旧了。让抽屉自己去拉详情，
         列表和抽屉就不会各持一份可能不一样的说法。 -->
    <AdminFeedbackDetailDrawer :feedback-id="selectedId" :open="drawerOpen" @update:open="close" />
  </div>
</template>

<style scoped>
/* 整页是一列定高：页头、Tab、工具条、状态条都不动，**滚动归表格自己领**。这是
   「操作台」和「阅读页」的分界 —— 阅读页整页一起滚，所以读到第八行时看不见列名，
   而这一页的表头永远在场。 */
.fbadmin {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  padding: 16px 24px 0;
}

.fbadmin__head {
  flex: 0 0 auto;
  padding-bottom: 8px;
}

/* 计数行。**`min-height` 是必需的**：这一行在加载完之前是空的，没有它的话数字到货
   那一刻页头会从 1 行长到 2 行，整张表跟着往下挪一次。 */
.fbadmin__sub {
  min-height: var(--lh-12);
  margin-top: 2px;
}

.fbadmin__rails {
  flex: 0 0 auto;
  border-bottom: 1px solid var(--line);
}

.fbadmin__tabs :deep(.v-tab) {
  /* 默认高度是 40（`density="compact"` 之后），压到 32：这一条是导航指示，不是内容，
     而它上面的页头和下面的工具条都在 40 以内，它比谁都高就没道理了。 */
  height: 32px;
  min-width: 0;
  padding: 0 12px;
  font-size: 13px;
  font-weight: 600;
  line-height: var(--lh-13);
  text-transform: none;
}

.fbadmin__tools {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 8px;
  height: 40px;
}

.fbadmin__search {
  flex: 0 0 auto;
  width: 280px;
}

.fbadmin__search :deep(.v-field) {
  /* 控件和工具条同高（32 / 40）：Vuetify 的 compact 字段是 40px，会把工具条撑到 48。 */
  height: 32px;
  font-size: 13px;
}

.fbadmin__search :deep(.v-field__input) {
  min-height: 32px;
  padding-top: 0;
  padding-bottom: 0;
  font-size: 13px;
}

/* 「清除」和「刷新」都是文字级的次要动作，做成 32px 的朴素按钮：琥珀在这一页
   一次都不出现（这一页没有主操作）。 */
.fbadmin__linkbtn {
  flex: 0 0 auto;
  height: 32px;
  padding: 0 8px;
  border: 0;
  border-radius: var(--radius-md);
  background: transparent;
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
  cursor: pointer;
  transition:
    background-color 0.12s ease,
    color 0.12s ease;
}

.fbadmin__linkbtn:hover {
  background: var(--fill);
  color: var(--text);
}

/* 分段控件。和外壳上那几块分区同一个形态（32px、radius-md、选中是中性 `--fill`
   底 + `--ink` 字）—— 同一套「在一组平级取值里选一个」的画法，不该有两种样子。 */
.fbadmin__seg {
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 2px;
  padding: 2px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
}

.fbadmin__seg-btn {
  height: 26px;
  padding: 0 10px;
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--muted);
  font-size: 13px;
  line-height: var(--lh-13);
  cursor: pointer;
  transition:
    background-color 0.12s ease,
    color 0.12s ease;
}

.fbadmin__seg-btn:hover {
  background: var(--fill);
  color: var(--text);
}

.fbadmin__seg-btn--on,
.fbadmin__seg-btn--on:hover {
  background: var(--fill);
  color: var(--ink);
  font-weight: 600;
}

.fbadmin__spacer {
  flex: 1 1 auto;
}

.fbadmin__alert {
  flex: 0 0 auto;
  margin-top: 12px;
}

.fbadmin__grid {
  margin-top: 12px;
}

.fbadmin__row {
  /* `::after` 那一层的定位基准。写在 `<tr>` 上 —— 表行做包含块在现代浏览器里是成立的
     （GitHub 的议题列表就是这么做的），代价见上面那段注释。 */
  position: relative;
  cursor: pointer;
}

.fbrow__link {
  /* 一条真的按钮，但看起来是正文：14/600/--ink，单行截断。 */
  display: block;
  max-width: 100%;
  overflow: hidden;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--ink);
  font: inherit;
  font-size: 14px;
  font-weight: 600;
  line-height: var(--lh-14);
  text-align: left;
  text-overflow: ellipsis;
  white-space: nowrap;
  cursor: pointer;
}

.fbrow__link::after {
  content: '';
  position: absolute;
  inset: 0;
}

/* 焦点环画在**整行**上，不是只画在标题那一小块上：焦点在哪里，读屏和眼睛都应该
   落在同一处。`outline-offset: -2px` 是为了不被卡片的边缘切掉（正 offset 会画到
   卡片外面去，被 `overflow` 裁掉一半）。`:has` 不支持时退回按钮自己的全局焦点环，
   不算不可用。 */
.fb-row:has(.fbrow__link:focus-visible) {
  outline: 2px solid var(--focus-ring);
  outline-offset: -2px;
}

.fbadmin__cell {
  overflow: hidden;
  color: var(--text);
  font-size: 13px;
  line-height: var(--lh-13);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.fbadmin__id {
  /* `--muted` 而不是 `--faint`：ID 是这一页里会被复制粘贴的那串字，而 `--faint`
     在 `--surface` 上只有 2.64:1（`--muted` 是 5.12），12px 正文要的是 4.5。
     [#1328] 把两档中性色一起调暗之后 `--faint` 会升到 4.33 —— 仍然在 4.5 下面，
     所以这一行不用跟着改。 */
  color: var(--muted);
  font-family: var(--font-mono, monospace);
  font-size: 12px;
}

.fbadmin__user {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  max-width: 100%;
}

.fbadmin__handle {
  overflow: hidden;
  text-overflow: ellipsis;
}

.fbadmin__lock {
  flex: 0 0 auto;
  color: var(--faint);
}

.fbadmin__dim {
  color: var(--muted);
}

.fbadmin__pri {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  line-height: var(--lh-12);
}

.fbadmin__num {
  font-variant-numeric: tabular-nums;
  text-align: right;
}

.fbadmin__when {
  color: var(--muted);
  font-size: 12px;
}

.fbadmin__pager {
  display: inline-flex;
  gap: 8px;
}
</style>
