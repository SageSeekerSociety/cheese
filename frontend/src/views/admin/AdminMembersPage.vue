<script setup lang="ts">
import type { AdminCandidate, PlatformAdminsPayload } from '@/types'

import { nextTick, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import AdminMembersPageView from './AdminMembersPageView.vue'

import { addPlatformAdmin, listPlatformAdmins, removePlatformAdmin, searchAdminCandidates } from '@/api'

// 管理后台的「成员管理」（`/admin/members`）：平台管理员的名单，页面上加与删。
// 取数与写在页面里，画法在 `AdminMembersPageView.vue`。
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
// 这一版换的是**壳**，不是做法：页头改用后台共用的那一份、错误与提示改用 token 画的一条
// 横条、头像从 `components/feedback/FeedbackAuthorAvatar` 换成
// `components/common/UserAvatar`（后台不该依赖反馈那个目录），窄屏交给 `BaseTable` 的
// 卡片模式。这些画法都在 `AdminMembersPageView.vue`，名字、状态列、agent 徽章的判据
// 也一并在那儿。
//
// 「我是不是管理员」不在这里问：外壳（`AdminLayout`）已经问过并且把子页挡在门后了。
// 这一页能画出来，就说明这个人过了那道门。
defineOptions({ name: 'AdminMembersPage' })

const { t } = useI18n()

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
    error.value = message(e, t('members.error.loadFailed'))
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
      error.value = message(e, t('members.error.searchFailed'))
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
      refused.push(
        t('members.notice.refusedEntry', { handle: person.handle, reason: message(e, t('members.error.addFailed')) })
      )
    }
  }
  adding.value = false
  // 连接符也走词条（`、` vs `, `、`；` vs `; `），不在代码里写死某一种语言的标点。
  if (done.length) notice.value = t('members.notice.added', { handles: done.join(t('members.notice.listJoin')) })
  if (refused.length) error.value = refused.join(t('members.notice.refusedJoin'))
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
    notice.value = answer.removed
      ? t('members.notice.removed', { handle })
      : t('members.notice.alreadyAbsent', { handle })
  } catch (e) {
    error.value = message(e, t('members.error.removeFailed'))
  } finally {
    removing.value = null
    confirmHandle.value = null
  }
}

/** 打开确认框的那颗「移出」。**自己记一下**，因为 Vuetify 的 `VDialog` 在没有
 *  `activator`（这里是 `v-model` 驱动的，没有触发元素插槽）时不会把焦点还回去 ——
 *  关掉之后焦点落到 `body`，键盘用户被扔回页面开头，得重新 Tab 一路找回来。 */
let removeTrigger: HTMLElement | null = null

function askRemove(event: Event, handle: string) {
  removeTrigger = event.currentTarget as HTMLElement | null
  confirmHandle.value = handle
}

// 确认框关掉（取消 / Esc / 点外面 / 移出成功）时把焦点送回触发它的那颗按钮。
// **移出成功那一支不送**：那一行连同按钮已经没了（`isConnected` 为假），focus 一个
// 脱离文档的节点是空操作，还不如留给 Vuetify 自己的收尾。
watch(confirmHandle, async (now, was) => {
  if (!was || now) return
  await nextTick()
  if (removeTrigger?.isConnected) removeTrigger.focus()
})

onMounted(load)
</script>

<template>
  <AdminMembersPageView
    v-model:dialog-open="dialogOpen"
    v-model:search="search"
    v-model:selected="selected"
    v-model:confirm-handle="confirmHandle"
    v-model:notice="notice"
    :loading="loading"
    :roster="roster"
    :error="error"
    :candidates="candidates"
    :searching="searching"
    :adding="adding"
    :removing="removing"
    @load="load"
    @open-dialog="openDialog"
    @add-selected="addSelected"
    @remove="remove"
    @ask-remove="askRemove"
  />
</template>
