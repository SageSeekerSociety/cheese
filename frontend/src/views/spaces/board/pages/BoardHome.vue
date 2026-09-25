<script setup lang="ts">
// 空间首页 —— 所有角色看到的同一个列表，差别只在顶上那句话和能不能看到「待审核」。
//
// 设计上刻意**不**按角色切列表内容：板上的题谁能看是「可见性」问题（已经在
// `TaskVisibilityService` 里），不是角色问题。角色只决定**能做什么**（发题、审题、
// 看汇总），不决定**能看什么**。把这个分清楚，界面就不会长出一堆按角色复制的列表。
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import PageBar from '../components/PageBar.vue'
import TaskCard from '../components/TaskCard.vue'
import { type BoardTask, isOpen } from '../model'
import { boardTasks, isManager, kpis, loadPending, me, space } from '../store'

type SortKey = 'hot' | 'new' | 'deadline'

/** 一页 20 道。3 列网格下 20 张是满满 7 行，再多就滚不到底了。 */
const PAGE_SIZE = 20

const route = useRoute()
const spaceId = computed(() => route.params.spaceId as string)

const keyword = ref('')
const category = ref<string | null>(null)
const sort = ref<SortKey>('hot')
const onlyOpen = ref(false)
const page = ref(1)

/** 待审队列。**单独拉一次**：`GET /tasks` 那条列表对非管理员本来就不含待审题，
 *  而这一块只有管理员看得见，所以按需取，不混进主页那份列表。
 *
 *  用 `watch` 而不是在这里直接判一次：空间是外壳异步装进来的，本页 setup 跑的那一刻
 *  `isManager` 还是 false（角色是从 `space.admins` 算出来的）—— 直接判一次的话，
 *  管理员永远看不到这一块。 */
const pending = ref<BoardTask[]>([])
watch(
  isManager,
  async (manager) => {
    if (manager && !pending.value.length) pending.value = await loadPending()
  },
  { immediate: true }
)

/** 分类选项从**板上真有的分类**里取，不用另一份名单 —— 否则筛选会出现「选了却
 *  一道题都没有」的空分类。 */
const categories = computed(() =>
  [...new Set(boardTasks.value.map((t) => t.category).filter(Boolean))].sort((a, b) => a.localeCompare(b, 'zh'))
)

const visible = computed(() => {
  let list = [...boardTasks.value]
  if (category.value) list = list.filter((t) => t.category === category.value)
  if (onlyOpen.value) list = list.filter((t) => isOpen(t))
  const kw = keyword.value.trim().toLowerCase()
  if (kw) {
    list = list.filter((t) => t.title.toLowerCase().includes(kw) || t.summary.toLowerCase().includes(kw))
  }
  if (sort.value === 'hot') list.sort((a, b) => b.claimCount - a.claimCount)
  if (sort.value === 'new')
    list.sort(
      (a, b) => new Date(b.publishedAt ?? b.createdAt).getTime() - new Date(a.publishedAt ?? a.createdAt).getTime()
    )
  if (sort.value === 'deadline') list.sort((a, b) => deadlineMs(a) - deadlineMs(b))
  return list
})

/** 没有截止日期的排最后（`Infinity` 而不是 0 —— 0 会把它排到最前面）。 */
function deadlineMs(t: BoardTask): number {
  return t.deadline ? new Date(t.deadline).getTime() : Number.POSITIVE_INFINITY
}

// 翻页只切一刀，筛选与排序口径一个字都不改：页码是**视图**上的东西，不是查询条件。
const paged = computed(() => visible.value.slice((page.value - 1) * PAGE_SIZE, page.value * PAGE_SIZE))

// 换关键词、分类、排序、只看还能领的 —— 都要回到第 1 页，否则会停在一个空的第 4 页上。
watch([keyword, category, sort, onlyOpen], () => (page.value = 1))
// 题目被审掉/删掉之后总页数会缩，落在界外的页码要收回来。
watch(
  () => Math.max(1, Math.ceil(visible.value.length / PAGE_SIZE)),
  (last) => {
    if (page.value > last) page.value = last
  }
)

function setPage(next: number) {
  page.value = next
  document.querySelector('.home__grid')?.scrollIntoView({ block: 'start' })
}

const closingSoon = computed(() =>
  boardTasks.value.filter((t) => {
    if (!isOpen(t) || !t.deadline) return false
    return new Date(t.deadline).getTime() - Date.now() < 3 * 86_400_000
  })
)

/** 待审队列里属于「我出的」那几道。管理员自己出的题自己能审，所以这句话要在首页说出来。 */
const myPending = computed(() => pending.value.filter((t) => t.publisher.handle === me.value.handle))
</script>

<template>
  <div class="home">
    <div class="home__hero">
      <div>
        <h1 class="home__title">{{ space?.name ?? '空间' }}</h1>
        <p class="home__sub">任何人都可以出题。题目发出后由所有者或管理员审核，通过后上板，谁都能领。</p>
      </div>
      <div class="home__hero-actions">
        <!-- 发题暂时走真平台已有那一页（口径也还是现状：管理员专属）。见 routes.ts。 -->
        <v-btn
          v-if="isManager"
          color="primary"
          variant="flat"
          prepend-icon="mdi-plus"
          :to="{ name: 'SpacesDetailPublishTask', params: { spaceId } }"
        >
          出题目
        </v-btn>
      </div>
    </div>

    <!-- 只给管理员看的一条待办：他们才是要动手审的人。普通用户看不到这一块。 -->
    <v-alert
      v-if="isManager && pending.length"
      class="home__todo"
      variant="tonal"
      color="warning"
      density="comfortable"
      :title="`有 ${pending.length} 道题在等你审`"
    >
      <template #append>
        <v-btn variant="tonal" size="small" :to="{ name: 'SpaceBoardReview', params: { spaceId } }">去审核</v-btn>
      </template>
      <span class="home__todo-text">
        <template v-if="myPending.length">
          其中 {{ myPending.length }} 道是你自己出的 —— <b>自己出的题自己也能审</b>，不必等别人。
        </template>
        <template v-else>审过之后题才会上板，作者在等。</template>
      </span>
    </v-alert>

    <div class="home__stats">
      <span
        >板上 <b>{{ kpis.published }}</b> 道题</span
      >
      <span
        >共 <b>{{ kpis.claims }}</b> 次领取</span
      >
      <span v-if="closingSoon.length" class="home__stats-warn">{{ closingSoon.length }} 道即将截止</span>
    </div>

    <div class="home__filters">
      <v-text-field
        v-model="keyword"
        autocomplete="off"
        density="compact"
        variant="outlined"
        hide-details
        prepend-inner-icon="mdi-magnify"
        placeholder="搜题目"
        class="home__search"
      />
      <v-select
        v-model="category"
        autocomplete="off"
        :items="categories"
        density="compact"
        variant="outlined"
        hide-details
        clearable
        placeholder="全部分类"
        class="home__select"
      />
      <v-btn-toggle v-model="sort" density="compact" variant="outlined" divided mandatory>
        <v-btn value="hot" size="small">最热</v-btn>
        <v-btn value="new" size="small">最新</v-btn>
        <v-btn value="deadline" size="small">快截止</v-btn>
      </v-btn-toggle>
      <v-checkbox v-model="onlyOpen" label="只要还能领的" density="compact" hide-details class="home__check" />
    </div>

    <div v-if="visible.length" class="home__grid">
      <TaskCard v-for="task in paged" :key="task.id" :task="task" show-publisher />
    </div>
    <v-empty-state
      v-else
      icon="mdi-clipboard-text-search-outline"
      title="没有符合条件的题目"
      text="换个关键词或清掉筛选再看看。"
    />

    <PageBar :page="page" :page-size="PAGE_SIZE" :total="visible.length" @update:page="setPage" />

    <p v-if="!isManager" class="home__foot">
      「审核」「成员」「数据看板」是所有者和管理员才有的入口，所以你这个身份看不到。
    </p>
  </div>
</template>

<style scoped lang="scss">
.home__hero {
  display: flex;
  gap: 16px;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 12px;
}

.home__title {
  margin: 0;
  font-size: 1.5rem;
  font-weight: 650;
}

.home__sub {
  margin: 6px 0 0;
  color: rgba(var(--v-theme-on-surface), 0.6);
  font-size: 0.86rem;
}

.home__todo {
  margin: 14px 0;
}

.home__todo-text {
  font-size: 0.8rem;
}

.home__stats {
  display: flex;
  flex-wrap: wrap;
  gap: 18px;
  padding: 10px 0;
  margin: 6px 0 12px;
  color: rgba(var(--v-theme-on-surface), 0.55);
  font-size: 0.8rem;
  border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.07);
}

.home__stats b {
  color: rgba(var(--v-theme-on-surface), 0.92);
}

.home__stats-warn {
  color: rgb(var(--v-theme-error));
}

.home__filters {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
  margin-bottom: 16px;
}

.home__search {
  max-width: 260px;
}

.home__select {
  max-width: 190px;
}

.home__check {
  flex: 0 0 auto;
}

.home__grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(330px, 1fr));
  gap: 14px;
}

.home__foot {
  margin-top: 22px;
  color: rgba(var(--v-theme-on-surface), 0.45);
  font-size: 0.78rem;
}
</style>
