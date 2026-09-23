<script setup lang="ts">
// 题目板首页 —— 所有角色看到的同一个列表，差别只在顶上那句话和能不能看到「待审核」。
//
// 设计上刻意**不**按角色切列表内容：板上的题谁能看是「可见性」问题（已经在
// `TaskVisibilityService` 里），不是角色问题。角色只决定**能做什么**（发题、审题、
// 看汇总），不决定**能看什么**。把这个分清楚，界面就不会长出一堆按角色复制的列表。
import { computed, ref, watch } from 'vue'

import PageBar from '../components/PageBar.vue'
import TaskCard from '../components/TaskCard.vue'
import { CATEGORIES, isOpen } from '../fixtures'
import { boardTasks, isManager, kpis, me, pendingTasks } from '../store'

type SortKey = 'hot' | 'new' | 'deadline'

/** 一页 20 道。3 列网格下 20 张是满满 7 行，再多就滚不到底了。 */
const PAGE_SIZE = 20

const keyword = ref('')
const category = ref<string | null>(null)
const sort = ref<SortKey>('hot')
const onlyOpen = ref(false)
const page = ref(1)

const visible = computed(() => {
  let list = [...boardTasks.value]
  if (category.value) list = list.filter((t) => t.category === category.value)
  if (onlyOpen.value) list = list.filter((t) => isOpen(t))
  const kw = keyword.value.trim().toLowerCase()
  if (kw) {
    list = list.filter(
      (t) =>
        t.title.toLowerCase().includes(kw) ||
        t.summary.toLowerCase().includes(kw) ||
        t.tags.some((tag) => tag.toLowerCase().includes(kw))
    )
  }
  if (sort.value === 'hot') list.sort((a, b) => b.claims.length - a.claims.length)
  if (sort.value === 'new')
    list.sort(
      (a, b) => new Date(b.publishedAt ?? b.createdAt).getTime() - new Date(a.publishedAt ?? a.createdAt).getTime()
    )
  if (sort.value === 'deadline') list.sort((a, b) => new Date(a.deadline).getTime() - new Date(b.deadline).getTime())
  return list
})

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
    const ms = new Date(t.deadline).getTime() - Date.now()
    return isOpen(t) && ms < 3 * 86_400_000
  })
)

/** 待审队列里属于「我出的」那几道。管理员自己出的题自己能审，所以这句话要在首页说出来。 */
const myPending = computed(() => pendingTasks.value.filter((t) => t.publisher.handle === me.value.handle))
</script>

<template>
  <div class="home">
    <div class="home__hero">
      <div>
        <h1 class="home__title">题目板</h1>
        <p class="home__sub">任何人都可以出题。题目发出后由所有者或管理员审核，通过后上板，谁都能领。</p>
      </div>
      <div class="home__hero-actions">
        <v-btn color="primary" variant="flat" prepend-icon="mdi-plus" to="/publish">出题目</v-btn>
      </div>
    </div>

    <!-- 只给管理员看的一条待办：他们才是要动手审的人。普通用户看不到这一块。 -->
    <v-alert
      v-if="isManager && pendingTasks.length"
      class="home__todo"
      variant="tonal"
      color="warning"
      density="comfortable"
      :title="`有 ${pendingTasks.length} 道题在等你审`"
    >
      <template #append>
        <v-btn variant="tonal" size="small" to="/review">去审核</v-btn>
      </template>
      <span class="home__todo-text">
        <template v-if="myPending.length">
          其中 {{ myPending.length }} 道是你自己出的 —— <b>自己出的题自己也能审</b>，不必等别人。
        </template>
        <template v-else>审过之后题才会上板，作者在等。</template>
      </span>
    </v-alert>
    <v-alert
      v-else-if="!isManager && pendingTasks.some((t) => t.publisher.handle === me.handle)"
      class="home__todo"
      variant="tonal"
      color="info"
      density="comfortable"
      title="你的题目正在排队等审核"
    >
      <span class="home__todo-text">审核通过后会自己出现在下面这张列表里。</span>
    </v-alert>

    <div class="home__stats">
      <span
        >板上 <b>{{ kpis.published }}</b> 道题</span
      >
      <span
        >共 <b>{{ kpis.claims }}</b> 次领取</span
      >
      <span
        >参与 <b>{{ kpis.participants }}</b> 人</span
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
        placeholder="搜题目、标签"
        class="home__search"
      />
      <v-select
        v-model="category"
        autocomplete="off"
        :items="CATEGORIES"
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
