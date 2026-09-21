<script setup lang="ts">
import type { AdminCandidate, PlatformAdminRow, PlatformAdminsPayload } from '@/api'

import { computed, onMounted, ref, watch } from 'vue'

import { addPlatformAdmin, listPlatformAdmins, removePlatformAdmin, searchAdminCandidates } from '@/api'
import FeedbackAuthorAvatar from '@/components/feedback/FeedbackAuthorAvatar.vue'
import { relTime } from '@/lib/relTime'

// 管理后台的「成员管理」（`/admin/members`）：平台管理员的名单，页面上加与删。
//
// 需求的原话是「方便后期动态添加管理人员」—— 以前那份名单只在部署配置里，改一个人
// 要有服务器权限。现在它分成两半：配置那份（根）仍然必填、页面上删不掉，页面上加的
// 进 `platform_admins` 表。**两个来源一个答案**，判据在服务端那一处
// （`AdminService.admin_handles`），这一页只画和发请求。
//
// 「根删不掉」这条规则不在这页上：删除按钮只画在 `added` 那几行，因为接口给的名单
// **本来就分两块**（`root` / `added`）。前端若把一个扁平数组按标记分组，分组规则就成
// 了第二份判据 —— 服务端哪天多一种来源，这里画不出来而且不会报错。
//
// 「我是不是管理员」不在这里问：外壳（`AdminLayout`）已经问过并且把子页挡在门后了。
// 这一页能画出来，就说明这个人过了那道门。
defineOptions({ name: 'AdminMembersPage' })

const roster = ref<PlatformAdminsPayload | null>(null)
const loading = ref(true)
/** 上一次失败**原话**。页面直接显示它，不另写一句「操作失败」—— 服务端已经说了
 *  是「平台里没有 handle 是 X 的账号」还是「X 是 agent」，重写一遍只会丢信息。 */
const error = ref<string | null>(null)
/** 加/删成功之后的**结果**：加了几个人、谁被拒了。成功也要说话 —— 一次加了三个、
 *  其中一个被拒的话，只画名单变化等于让人自己去比对哪三个是刚才那些。 */
const notice = ref<string | null>(null)

const dialogOpen = ref(false)
/** 输入框里那串字。搜索在服务端（用户目录接口只在它取回的那一页里过滤，搜不到人），
 *  所以这里要拿着它去打接口，不能交给 `v-autocomplete` 本地过滤。 */
const search = ref('')
const candidates = ref<AdminCandidate[]>([])
const searching = ref(false)
const selected = ref<AdminCandidate[]>([])
const adding = ref(false)
/** 正在删的那一行（`handle`）：删除要等确认，也要防连点。 */
const removing = ref<string | null>(null)
const confirmHandle = ref<string | null>(null)

const added = computed<PlatformAdminRow[]>(() => roster.value?.added ?? [])
const root = computed<string[]>(() => roster.value?.root ?? [])

/** 后端把能读的原因写在 `message` 里（`ApiError` 带上来的），照它显示 —— 上面那句
 *  「显示原话」的意思就是这里不加工。`fallback` 只在拿不到那句话时用。 */
function message(e: unknown, fallback: string): string {
  return e instanceof Error && e.message ? e.message : fallback
}

async function load() {
  loading.value = true
  error.value = null
  try {
    roster.value = await listPlatformAdmins()
  } catch (e) {
    error.value = message(e, '管理员名单加载失败')
  } finally {
    loading.value = false
  }
}

// 搜索防抖：每敲一个字打一次接口，一次页面停留会打出十几个请求，而结果只看得见最后
// 一个。250ms 是「停手了才问」的那个间隔。
let searchTimer: ReturnType<typeof setTimeout> | null = null
watch(search, (q) => {
  if (searchTimer) clearTimeout(searchTimer)
  const wanted = q.trim()
  if (!wanted) {
    // 空串**不发请求**：接口会拿它回「平台的前 20 个账号」，那不是一个搜索结果。
    candidates.value = []
    return
  }
  searching.value = true
  searchTimer = setTimeout(async () => {
    try {
      const page = await searchAdminCandidates(wanted)
      // 慢的那个请求后到，会把新的搜索结果盖掉。只认当前这串字的答案。
      if (search.value.trim() === wanted) candidates.value = page.items
    } catch (e) {
      candidates.value = []
      error.value = message(e, '搜账号失败')
    } finally {
      searching.value = false
    }
  }, 250)
})

function openDialog() {
  selected.value = []
  search.value = ''
  candidates.value = []
  notice.value = null
  dialogOpen.value = true
}

async function addSelected() {
  adding.value = true
  error.value = null
  const done: string[] = []
  const refused: string[] = []
  // 一次一个地发：接口一次加一个人，而且**每个人各有一套拒绝理由**（不存在、是
  // agent、本来就在根名单里）。并发发出去的话，失败的那几个只剩下一个顺序不定的
  // 列表，说不清是谁被拒了。
  for (const person of selected.value) {
    try {
      roster.value = await addPlatformAdmin(person.handle)
      done.push(person.handle)
    } catch (e) {
      refused.push(`${person.handle}：${message(e, '没加成')}`)
    }
  }
  adding.value = false
  if (done.length) notice.value = `已添加：${done.join('、')}`
  if (refused.length) error.value = refused.join('；')
  // 全部加成功才关：有被拒的还留在框里，人能直接改选择再试一次，不用重开对话框。
  if (!refused.length) dialogOpen.value = false
}

async function remove() {
  const handle = confirmHandle.value
  if (!handle) return
  removing.value = handle
  error.value = null
  try {
    const answer = await removePlatformAdmin(handle)
    roster.value = answer
    // 删一个不在名单里的人不是错误（他的目的已经成立），但页面要说得出这次没删着
    // 东西 —— 静默成功会让人以为按钮坏了。
    notice.value = answer.removed ? `已移出：${handle}` : `${handle} 本来就不在名单里`
  } catch (e) {
    error.value = message(e, '没移出去')
  } finally {
    removing.value = null
    confirmHandle.value = null
  }
}

onMounted(load)
</script>

<template>
  <!-- 滚动归这一页自己领（仓库约定，见 styles/common.scss）。 -->
  <div class="admin-members fill-height overflow-y-auto">
    <div class="admin-members__inner page-container--wide">
      <header class="admin-members__head">
        <div>
          <h1 class="t-page-title">成员管理</h1>
          <div class="t-meta">
            这些人能看所有私密反馈和安全问题，也能在这里加别人 —— 能改这份名单就等于能给自己开门。
          </div>
        </div>
        <v-spacer />
        <v-btn color="primary" prepend-icon="mdi-account-plus-outline" @click="openDialog">添加管理员</v-btn>
      </header>

      <v-alert v-if="error" type="error" density="compact" variant="tonal" class="mb-4">
        {{ error }}
        <template #append>
          <v-btn variant="text" size="small" @click="load">重试</v-btn>
        </template>
      </v-alert>
      <v-alert
        v-else-if="notice"
        type="success"
        density="compact"
        variant="tonal"
        class="mb-4"
        closable
        @click:close="notice = null"
      >
        {{ notice }}
      </v-alert>

      <div v-if="loading" class="t-meta py-8">加载中…</div>

      <template v-else-if="roster">
        <section class="admin-members__block">
          <div class="t-title mb-1">部署配置里的根管理员</div>
          <div class="t-meta mb-3">
            来自部署配置（<code>PLATFORM_ADMIN_HANDLES</code>），<strong>页面上删不掉</strong> ——
            能在这里被清空的名单没有回头的路，改它要有服务器权限
          </div>
          <div class="d-flex flex-wrap ga-2">
            <span v-for="handle in root" :key="handle" class="chip-neutral">{{ handle }}</span>
            <span v-if="!root.length" class="t-meta">配置里没写人（本地开发如此；部署时必须填）</span>
          </div>
        </section>

        <section class="admin-members__block">
          <div class="t-title mb-1">页面上添加的</div>
          <div class="t-meta mb-3">这些人随时可以移出名单。</div>
          <v-table v-if="added.length" hover class="admin-members__table">
            <tbody>
              <tr v-for="row in added" :key="row.handle">
                <td class="am-td am-td--who">
                  <div class="d-flex align-center ga-2">
                    <FeedbackAuthorAvatar :handle="row.handle" :size="22" />
                    <span>{{ row.handle }}</span>
                  </div>
                </td>
                <td class="am-td t-meta">{{ row.added_by_handle }} 加的 · {{ relTime(row.created_at) }}</td>
                <td class="am-td am-td--actions">
                  <v-btn
                    variant="text"
                    size="small"
                    color="secondary"
                    :loading="removing === row.handle"
                    @click="confirmHandle = row.handle"
                  >
                    移出
                  </v-btn>
                </td>
              </tr>
            </tbody>
          </v-table>
          <div v-else class="t-meta py-4">还没有在页面上加过管理员 —— 现在名单上的人全部来自部署配置。</div>
        </section>
      </template>
    </div>

    <!-- 加人的框照「成员页面邀请」那一套：按钮 → 对话框 → 可搜的多选 + 添加。
         区别只有一个，是数据来源：那边的人选是一次拉回来的项目成员，这里上千个账号，
         所以搜索走服务端（`/admin/users?q=`）。

         `:no-filter="true"` 是这件事的**另一半**，不加就等于没做（同 TopicSelector）：
         `v-autocomplete` 默认还会拿输入串再筛一次自己的 `items`，而筛的是
         `item-title` —— 这里正是 handle。于是**按昵称搜不出人**：服务端把「彭文博」
         查成了 `pengwenbo` 并回了一行，组件再拿「彭文博」去比 `pengwenbo`，不匹配，
         当场筛掉。表现是「输入 handle 搜得到、输入中文名搜不到」，而接口、fixture、
         服务端用例三处各自看都对。 -->
    <v-dialog v-model="dialogOpen" max-width="520" persistent>
      <v-card rounded="lg">
        <v-card-title class="px-4 pt-4 pb-2">添加管理员</v-card-title>
        <v-card-text class="px-4">
          <v-autocomplete
            v-model="selected"
            v-model:search="search"
            autocomplete="off"
            label="搜索账号"
            placeholder="输入 handle 或昵称"
            variant="outlined"
            density="comfortable"
            :items="candidates"
            :loading="searching"
            :no-filter="true"
            item-title="handle"
            item-value="handle"
            return-object
            multiple
            chips
            closable-chips
            hide-no-data
          >
            <template #item="{ item, props }">
              <v-list-item v-bind="props" :disabled="item.raw.already_admin">
                <template #prepend>
                  <FeedbackAuthorAvatar :handle="item.raw.handle" :avatar-id="item.raw.avatar_id" :size="30" />
                </template>
                <v-list-item-title>{{ item.raw.nickname }}</v-list-item-title>
                <v-list-item-subtitle>{{ item.raw.handle }}</v-list-item-subtitle>
                <template #append>
                  <span v-if="item.raw.already_admin" class="chip-neutral">已经是管理员</span>
                </template>
              </v-list-item>
            </template>
          </v-autocomplete>
          <div class="t-meta mt-2">加进来的人马上就能看到私密反馈和安全问题，也能自己在这里加人。</div>
        </v-card-text>
        <v-card-actions class="pa-4 pt-0">
          <v-spacer />
          <v-btn variant="text" @click="dialogOpen = false">取消</v-btn>
          <v-btn color="primary" :loading="adding" :disabled="!selected.length" @click="addSelected">添加</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <!-- 移出要问一句：这个动作当场改的是**谁能看别人的私密反馈**，而且按钮就在一行
         名字旁边，点错了没有任何东西拦着。 -->
    <v-dialog :model-value="!!confirmHandle" max-width="420" @update:model-value="confirmHandle = null">
      <v-card rounded="lg">
        <v-card-title class="px-4 pt-4 pb-2">移出管理员</v-card-title>
        <v-card-text class="px-4">
          把 <strong>{{ confirmHandle }}</strong> 移出名单？他马上看不到私密反馈和安全问题，也不能再进来加人。
        </v-card-text>
        <v-card-actions class="pa-4 pt-0">
          <v-spacer />
          <v-btn variant="text" @click="confirmHandle = null">取消</v-btn>
          <v-btn color="primary" :loading="!!removing" @click="remove">移出</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </div>
</template>

<style scoped>
.admin-members {
  padding: 24px 16px 48px;
}
.admin-members__head {
  display: flex;
  align-items: flex-start;
  gap: 16px;
  margin-bottom: 20px;
}
.admin-members__block {
  margin-bottom: 28px;
}
/* 压过 Vuetify 自带的 th/td 规则，同管理端反馈表格（那边把 Vuetify 自己那几层写进
   选择器，权重正常赢，所以不用 !important）。 */
.admin-members__table :deep(.v-table__wrapper table) :is(tbody td) {
  height: auto;
  padding: 10px 8px;
}
.am-td--actions {
  text-align: right;
  width: 1%;
  white-space: nowrap;
}
/* 行内代码照 `.md-content code` 那一份：同一个仓库里嵌在正文里的代码块长一样。 */
code {
  font-family: var(--font-mono);
  background: var(--fill);
  padding: 0.5px 5px;
  border-radius: var(--radius-sm);
  font-size: 0.88em;
}
</style>
