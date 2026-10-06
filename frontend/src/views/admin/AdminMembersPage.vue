<script setup lang="ts">
import type { AdminCandidate, PlatformAdminsPayload } from '@/api'

import { onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import { useUserRefResolver } from '@/composables/useUserRefResolver'

import AdminMembersPageView from './AdminMembersPageView.vue'

import { addPlatformAdmin, listPlatformAdmins, removePlatformAdmin, searchAdminCandidates } from '@/api'

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
// 这一版改的四件事：
//
//   1. **整页文案进 i18n**（`members.*` 命名空间）。全部写成 `t('members.…')` 字面量、
//      一个键都不拼 —— i18n 闸门（`src/i18n/catalog.spec.ts`）照源码字面量认「这个键
//      有人用」，拼出来的键既不算调用、真叶子还会被判成没人引用。
//   2. **表从 4 列扩到 6 列**：账号状态、注册时间。名单要能回答「这行权限是不是
//      死的」—— 平台上没这个账号（或已注销）、这个 handle 是 agent，都是配置里写了
//      但永远用不上的权限。判据在服务端 `admins_out` 一次读里拼成显式字段
//      （`has_account` / `registered_at` / `is_agent`），这一页只画：正常行的状态格
//      画 `—` 不说话，异常才明画。
//   3. **who 列加 agent 徽章**。agent 做不了管理动作，一个 agent 行也是死权限。它
//      只会从根配置混进来（页面加人服务端拒 agent），所以徽章实际上只可能出现在根
//      那几行 —— 但判据按行画、不按组画：数据哪天从别处混进 added，这里照样画得出。
//   4. **刷新有 busy 态**：手上已有名单时再取数只把旧名单压暗（BaseTable 既有能力，
//      这页以前没用），不闪骨架。
//
// 这一版换的是**壳**，不是做法：页头改用后台共用的那一份（五个后台页各写一份页头，
// 字号和内边距各不相同，切分区时页头会跳）、错误与提示改用 token 画的一条横条（`v-alert`
// 的默认样和这一页的表格不是一套）、头像从 `components/feedback/FeedbackAuthorAvatar`
// 换成 `components/common/UserAvatar`（后台不该依赖反馈那个目录；这里只用到「有 id 就
// 拼 URL、没有就彩色首字母」那一半），窄屏交给 `BaseTable` 的卡片模式（390px 下六列
// 定宽合计 772px，横着滚的表在手机上是读不到右边那几列的）。
//
// 确认框正文是全仓第一处 `<i18n-t>`：「把 {handle} 移出名单？…」要回显 handle 加粗，
// 而英文语序和中文不同 —— 句子碎片键被 i18n.md §3 禁掉，插槽是唯一合规的写法。
//
// 「我是不是管理员」不在这里问：外壳（`AdminLayout`）已经问过并且把子页挡在门后了。
// 这一页能画出来，就说明这个人过了那道门。
//
// 后来页面拆成**容器 + 视图**两层：这一半留着上面这些数据和规矩，以及四个接口调用、
// 加人框的搜索（防抖、只要最后那串字的答案）、移出确认后的收尾；画面搬到
// `AdminMembersPageView.vue`，那边只吃 props、只往上发事件。行怎么摊平、哪一列在窄屏
// 收起来，在那一半里。人名也从 `UserRefLink` 换成纯展示的 `UserRef`：名字和去处由
// `useUserRefResolver` 在这里算好，当 `resolveUser` 递下去。
defineOptions({ name: 'AdminMembersPage' })

const { t } = useI18n()
const { resolve: resolveUser, navigate } = useUserRefResolver()

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

// 视图那边把 `v-model` 拆成 prop + 事件发上来（`v-autocomplete` 的 `search` 也一样），
// 这里只是把它们落回下面这几个 ref 上。
function setDialogOpen(open: boolean) {
  dialogOpen.value = open
}
function setSearch(q: string) {
  search.value = q
}
function setSelected(items: AdminCandidate[]) {
  selected.value = items
}
function setConfirmHandle(handle: string | null) {
  confirmHandle.value = handle
}

onMounted(load)
</script>

<template>
  <AdminMembersPageView
    :roster="roster"
    :loading="loading"
    :error="error"
    :notice="notice"
    :dialog-open="dialogOpen"
    :search="search"
    :candidates="candidates"
    :searching="searching"
    :selected="selected"
    :adding="adding"
    :removing="removing"
    :confirm-handle="confirmHandle"
    :resolve-user="resolveUser"
    @reload="load"
    @open-dialog="openDialog"
    @update:dialog-open="setDialogOpen"
    @update:search="setSearch"
    @update:selected="setSelected"
    @submit="addSelected"
    @update:confirm-handle="setConfirmHandle"
    @remove="remove"
    @dismiss-notice="notice = null"
    @navigate="navigate"
  />
</template>
