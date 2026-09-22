<script setup lang="ts">
import type { AdminCandidate, PlatformAdminRow, PlatformAdminsPayload } from '@/api'

import { computed, onMounted, ref, watch } from 'vue'

import { addPlatformAdmin, listPlatformAdmins, removePlatformAdmin, searchAdminCandidates } from '@/api'
import AdminGrid from '@/components/admin/AdminGrid.vue'
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
// 这一版改的三件事：
//
//   1. **两段裸结构收成一张表**。上一版是「一排芯片 + 一张两列表格」两段互不相干的
//      东西；两块说的都是「谁是管理员」，分开画就得让人自己把两处对起来。现在是一张
//      表里的两组，组头行是**这一组的名字和它的人数**。
//   2. **「删不掉」有形状**。上一版根那几行右边是空的，人要自己读上面那段说明才知道
//      「不是漏画了按钮」。现在那一格写着「不可移出」。
//   3. **「移出」是一个界内的按钮**，不是一行名字旁边的裸文字：两种操作权在同一个
//      视觉层级里，才看得出它们不一样。
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

/** 四条列。**只有「添加信息」那一列是 `null`**（自适应）—— `table-layout: fixed`
 *  下没有宽度的列会平分剩余空间，多给一列就散架。 */
const COLS: (string | null)[] = ['320px', '160px', null, '120px']
const BONE_WIDTHS = ['62%', '48%', '64%', '40%']

/** 表头下那段说明。**屏幕上一行、完整那句放 `title`**：它上一版占两行，两行说明后面
 *  跟着三行数据，读起来像文档不像后台。`PLATFORM_ADMIN_HANDLES` 这个变量名也从屏幕上
 *  移走了 —— 它对我们有用，对「想知道这些人是谁、能不能删」的人没用（§8.2：界面上不写
 *  实现细节）。 */
const SUBTITLE = '这些人能看所有私密反馈和安全问题，也能在这里加别人 —— 能改这份名单就等于能给自己开门。'
const SUBTITLE_TITLE =
  SUBTITLE +
  '「部署配置」那一组来自部署配置（PLATFORM_ADMIN_HANDLES），页面上删不掉：能在这里被清空的名单没有回头的路，改它要有服务器权限。'

const countLine = computed(() => (roster.value ? `共 ${root.value.length + added.value.length} 人` : ''))

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
  <div class="am">
    <header class="am__head">
      <h1 class="t-page-title">成员管理</h1>
      <p class="am__sub t-meta" :title="SUBTITLE_TITLE">{{ SUBTITLE }}</p>
    </header>

    <div class="am__tools">
      <span class="t-meta">{{ countLine }}</span>
      <div class="am__spacer" />
      <v-btn icon="mdi-refresh" variant="text" size="small" aria-label="刷新" :loading="loading" @click="load" />
      <!-- 全页唯一一块琥珀：这一页确实有一个主操作，而它就是这个。 -->
      <v-btn color="primary" size="small" prepend-icon="mdi-account-plus-outline" @click="openDialog">
        添加管理员
      </v-btn>
    </div>

    <v-alert v-if="error" type="error" density="compact" variant="tonal" class="am__alert">
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
      class="am__alert"
      closable
      @click:close="notice = null"
    >
      {{ notice }}
    </v-alert>

    <div class="am__gridwrap">
      <AdminGrid
        label="平台管理员名单"
        :cols="COLS"
        :bone-widths="BONE_WIDTHS"
        :loading="loading && !roster"
        :skeleton-rows="4"
      >
        <template #head>
          <tr>
            <th scope="col">管理员</th>
            <th scope="col">来源</th>
            <th scope="col">添加信息</th>
            <th scope="col" class="am__num">操作</th>
          </tr>
        </template>

        <!-- 组头行。**名字和人数是两个元素**，不是一个字符串拼出来的：它们是两种
             东西（这一组叫什么 / 有几个人），拼在一起会让读屏把它们念成一串数字
             加名字，也让人没法单独抓那个数。 -->
        <tr class="am__group">
          <td colspan="4" class="am__groupcell">
            <span class="am__grouplabel">部署配置里的根管理员</span>
            <span class="am__groupcount">{{ root.length }}</span>
          </td>
        </tr>

        <tr v-for="handle in root" :key="handle" class="am__row">
          <td class="am__cell">
            <span class="am__who">
              <FeedbackAuthorAvatar :handle="handle" :size="20" />
              <span>{{ handle }}</span>
            </span>
          </td>
          <td class="am__cell"><span class="am__dim">部署配置</span></td>
          <td class="am__cell"><span class="am__dim">—</span></td>
          <!-- 这一格上一版是空的，人要读完上面那段说明才知道「不是漏画了按钮」。
               写出来比留白省一次阅读。 -->
          <td class="am__cell am__cell--actions"><span class="am__dim">不可移出</span></td>
        </tr>
        <tr v-if="!root.length" class="am__row">
          <td colspan="4" class="am__cell am__none">配置里没写人（本地开发如此；部署时必须填）</td>
        </tr>

        <tr class="am__group">
          <td colspan="4" class="am__groupcell">
            <span class="am__grouplabel">页面上添加的</span>
            <span class="am__groupcount">{{ added.length }}</span>
          </td>
        </tr>

        <tr v-for="row in added" :key="row.handle" class="am__row">
          <td class="am__cell">
            <span class="am__who">
              <FeedbackAuthorAvatar :handle="row.handle" :size="20" />
              <span>{{ row.handle }}</span>
            </span>
          </td>
          <td class="am__cell"><span class="am__dim">页面添加</span></td>
          <td class="am__cell">
            <span class="am__dim">{{ row.added_by_handle }} 加的 · {{ relTime(row.created_at) }}</span>
          </td>
          <td class="am__cell am__cell--actions">
            <!-- 描边（不是实心、不是文字按钮）：这个动作改的是「谁能看别人的私密
                 反馈」，它得看得出来是一个真正界内的按钮，而不是一行可以随手划过的
                 文字。确认在同名的对话框里。（**没有 `aria-label`** —— 加了会把可及
                 名字覆盖掉，读屏念的就不再是「移出」这两个字了。） -->
            <v-btn
              variant="outlined"
              size="small"
              :loading="removing === row.handle"
              @click="confirmHandle = row.handle"
            >
              移出
            </v-btn>
          </td>
        </tr>
        <tr v-if="!added.length" class="am__row">
          <td colspan="4" class="am__cell am__none">还没有在页面上加过管理员 —— 现在名单上的人全部来自部署配置。</td>
        </tr>
      </AdminGrid>
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
/* 和反馈管理同一套页头 / 工具条 / 表格三段式，`padding` 也逐字相同 —— 两块是同一个
   后台的两个分区，切换时页头不该跳一下。 */
.am {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  padding: 16px 24px 0;
}

.am__head {
  flex: 0 0 auto;
  padding-bottom: 8px;
}

/* 一行说完。`min-height` 是给「名单还没回来」那一帧留位，否则数字到货时页头会长一行。 */
.am__sub {
  overflow: hidden;
  min-height: var(--lh-12);
  margin-top: 2px;
  max-width: 680px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.am__tools {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 8px;
  height: 40px;
  border-bottom: 1px solid var(--line);
}

.am__spacer {
  flex: 1 1 auto;
}

.am__alert {
  flex: 0 0 auto;
  margin-top: 12px;
}

/* 名单只有几行，所以这一张卡**贴着内容**，不撑满剩下的高度（反馈那一页相反：它一屏
   十七行，表格自己领滚动）。外面的这一层负责「能缩」—— 名单长起来时它先让位，然后
   表格内部才滚。 */
.am__gridwrap {
  display: flex;
  flex: 0 1 auto;
  min-height: 0;
  margin-top: 12px;
}

.am__group {
  background: var(--fill-2);
}

/* 组头那一格的**内边距和行高都不另写**：表壳给 8px 上下 × 12px 左右，行高也由它
   钉成和数据行一样。原来这里写的是 `padding: 0 12px` + `height: 32px` —— 表壳的
   规则压得过它，那两条从来没生效过，留着只会误导。 */
.am__groupcell {
  display: flex;
  align-items: center;
  gap: 8px;
}

.am__grouplabel {
  color: var(--muted);
  font-size: 12px;
  font-weight: 600;
  line-height: var(--lh-12);
}

.am__groupcount {
  color: var(--faint);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  line-height: var(--lh-12);
}

/* 行的内边距、下边线、悬停底色、圆角都由表壳给
   （`components/admin/AdminGrid.vue` 的 `.agrid__body :deep(td)` 那一段），
   这里只写「这一格里的字怎么排」。**别在这里重复写内边距**：表壳那条规则按
   `tbody td` 选，压得过这一层的 `.am__cell`，写了也是白写 —— 反而会让下一个人
   以为行高是从这儿来的（第一版就是这样，页面里写着 4px、页面上跑的是 8px）。 */
.am__cell {
  overflow: hidden;
  color: var(--text);
  font-size: 13px;
  line-height: var(--lh-13);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.am__cell--actions {
  text-align: right;
}

.am__who {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  max-width: 100%;
}

.am__dim {
  color: var(--muted);
}

/* 空态那一格想要比一行高一点，所以**真的需要**压过表壳那 8px —— 多写一层 `.am`
   （三个类 `(0,3,0)` > 表壳的 `(0,2,1)`，理由写在 `AdminGrid.vue` 顶上那段注释里）。
   这是这个写法在本仓库的实例，照着写就行。 */
.am .am__none {
  padding: 20px 12px;
  color: var(--faint);
  text-align: center;
}

.am__num {
  text-align: right;
}
</style>
