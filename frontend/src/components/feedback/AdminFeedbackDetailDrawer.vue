<script setup lang="ts">
import type { FeedbackPriority, FeedbackStatus } from '@/cx_types'

import { computed, ref, watch } from 'vue'

import FeedbackAuthorAvatar from './FeedbackAuthorAvatar.vue'
import FeedbackStatusChip from './FeedbackStatusChip.vue'

import { KIND_LABEL, PRIORITY_META, SOURCE_LABEL, statusMeta } from '@/lib/feedbackMeta'
import { relTime } from '@/lib/relTime'
import { useFeedbackStore } from '@/stores/feedback'

// 管理员列表点开之后右侧那一栏。
//
// 需求明写：**不要提供「管理员将私密反馈转公开」的按钮**。这里因此也没有这个动作，
// 而且不是「先不做」—— 是不该做：公开与否是提交者当场做的决定，管理员能改它就等于
// 那个决定是假的。服务端的 `PATCH /admin/feedback/{id}` 也不接受 `visibility`，
// 所以这不是前端客气一下，是链路上根本没有这个口子。
//
// 这一栏**自己拉详情**（`store.loadAdminDetail`），不吃列表传进来的那一行：这里的
// 四个写操作服务端都回「刷新后的整条详情」（改安全问题还会顺手写一条时间线），
// 列表那份行数据在第一次点击之后就旧了。传 id 进来、内容现拉，列表和抽屉就不会
// 各持一份说法不一样的数据。
//
// 内部备注是**只增不改**的流水（服务端的 `notes` 数组），不是一份可以覆盖的文本字段。
// 原型上是一个 textarea + 保存：那意味着两个人同时写，后写的把先写的抹掉，而这张
// 表上没有任何地方看得出发生过这件事。
defineOptions({ name: 'AdminFeedbackDetailDrawer' })

const props = defineProps<{
  feedbackId: string | null
  open: boolean
}>()
const emit = defineEmits<{ (e: 'update:open', value: boolean): void }>()

const store = useFeedbackStore()

/** 正在看的那条详情。比 id 之外还比一次 `detailId`：慢响应回来时抽屉可能已经关了。 */
const item = computed(() =>
  props.feedbackId && store.detailId === props.feedbackId && store.detail ? store.detail : null
)

const noteDraft = ref('')
const assignDraft = ref('')

watch(
  () => [props.open, props.feedbackId] as const,
  ([open, id]) => {
    if (!open || !id) return
    noteDraft.value = ''
    assignDraft.value = ''
    void store.loadAdminDetail(id)
  },
  { immediate: true }
)

// 状态梯子用**服务端**那份（meta 里的 `status_ladder`）：管理员看到的顺序就是
// 这条反馈真会走的顺序，前端不另存一份。
const statusItems = computed(() => store.statusLadder.map((s) => ({ title: statusMeta(s).label, value: s })))
const priorityItems = computed(() =>
  Object.entries(PRIORITY_META).map(([value, meta]) => ({ title: meta.label, value }))
)

async function addNote() {
  const body = noteDraft.value.trim()
  if (!body || !item.value) return
  noteDraft.value = ''
  await store.addNote(item.value.id, body)
}

async function assign() {
  if (!item.value) return
  const handle = assignDraft.value.trim()
  assignDraft.value = ''
  await store.assign(item.value.id, handle || null)
}
</script>

<template>
  <v-navigation-drawer
    :model-value="open"
    temporary
    location="right"
    width="520"
    class="fb-admin-drawer"
    @update:model-value="(open: boolean) => !open && emit('update:open', false)"
  >
    <div class="fb-admin-drawer__inner">
      <div class="fb-admin-drawer__head">
        <div class="min-width-0">
          <div class="t-meta">
            <template v-if="item"
              >{{ item.display_id }} · {{ SOURCE_LABEL[item.author_is_agent ? 'agent' : 'user'] }}</template
            >
            <template v-else>反馈详情</template>
          </div>
          <div class="t-title">{{ item?.title ?? '加载中…' }}</div>
        </div>
        <v-spacer />
        <v-btn icon size="small" variant="text" aria-label="关闭" @click="emit('update:open', false)">
          <v-icon size="18">mdi-close</v-icon>
        </v-btn>
      </div>

      <div v-if="item" class="fb-admin-drawer__body">
        <!-- 处理动作放在最上面：打开这一栏是为了做事，不是为了读。 -->
        <div class="fb-admin-drawer__actions">
          <div class="fb-field">
            <div class="t-eyebrow mb-1">更新状态</div>
            <v-select
              :model-value="item.status"
              :items="statusItems"
              autocomplete="off"
              density="compact"
              hide-details
              @update:model-value="(v: FeedbackStatus) => store.setStatus(item!.id, v)"
            />
          </div>
          <div class="fb-field">
            <div class="t-eyebrow mb-1">优先级</div>
            <v-select
              :model-value="item.priority"
              :items="priorityItems"
              autocomplete="off"
              density="compact"
              hide-details
              @update:model-value="(v: FeedbackPriority) => store.setPriority(item!.id, v)"
            />
          </div>
          <div class="fb-field">
            <div class="t-eyebrow mb-1">指派处理</div>
            <!-- 自由输入而不是下拉：管理端拿不到一份「所有人」的名单（成员接口在项目
                 作用域里，这一页是平台级的），写死几个名字等于让名单随时过期。 -->
            <v-text-field
              v-model="assignDraft"
              :placeholder="item.assignee_handle ?? '填 handle，留空取消指派'"
              autocomplete="off"
              density="compact"
              hide-details
              @keyup.enter="assign"
              @blur="assignDraft.trim() && assign()"
            />
          </div>
        </div>

        <div class="d-flex align-center flex-wrap ga-2 mb-4">
          <FeedbackStatusChip :status="item.status" />
          <FeedbackStatusChip :priority="item.priority" />
          <span class="chip-neutral">{{ KIND_LABEL[item.kind] }}</span>
          <span v-if="item.visibility === 'private'" class="chip-neutral">
            <v-icon size="12">mdi-lock-outline</v-icon>私密
          </span>
          <span v-if="item.security" class="chip-neutral">
            <v-icon size="12">mdi-shield-alert-outline</v-icon>安全
          </span>
        </div>

        <div class="t-meta mb-4 d-flex align-center ga-2">
          <FeedbackAuthorAvatar :handle="item.author_handle" :is-agent="item.author_is_agent" :size="24" />
          <span>
            {{ item.author_handle }} · {{ relTime(item.created_at) }} · 支持 {{ item.supports }} · 评论
            {{ item.comments }}
          </span>
        </div>

        <section class="fb-section">
          <div class="t-eyebrow mb-1">用户描述</div>
          <p class="t-body fb-text">{{ item.problem }}</p>
          <template v-if="item.why">
            <div class="t-eyebrow mt-3 mb-1">为什么需要</div>
            <p class="t-body fb-text">{{ item.why }}</p>
          </template>
          <template v-if="item.expectation">
            <div class="t-eyebrow mt-3 mb-1">期望方案</div>
            <p class="t-body fb-text">{{ item.expectation }}</p>
          </template>
        </section>

        <section v-if="item.what_happened || item.repro || item.evidence" class="fb-section">
          <div class="t-eyebrow mb-1">现场</div>
          <template v-if="item.what_happened">
            <div class="fb-sub">发生了什么</div>
            <p class="t-body fb-text">{{ item.what_happened }}</p>
          </template>
          <template v-if="item.repro">
            <div class="fb-sub">复现步骤</div>
            <pre class="fb-pre">{{ item.repro }}</pre>
          </template>
          <template v-if="item.evidence">
            <div class="fb-sub">证据</div>
            <p class="t-body fb-text">{{ item.evidence }}</p>
          </template>
        </section>

        <section v-if="item.logs" class="fb-section">
          <div class="t-eyebrow mb-1">日志</div>
          <pre class="fb-pre">{{ item.logs }}</pre>
        </section>

        <section v-if="item.session_id || item.environment" class="fb-section">
          <div class="t-eyebrow mb-1">会话信息</div>
          <div v-if="item.session_id" class="fb-kv">
            <span class="t-meta">会话</span>
            <code class="fb-code">{{ item.session_id }}</code>
          </div>
          <div v-if="item.environment" class="fb-kv">
            <span class="t-meta">环境</span>
            <span class="t-body">{{ item.environment }}</span>
          </div>
        </section>

        <!-- 安全问题这一格单独放，且写成一句话说明后果：勾上之后这条对**同事**
             就不见了（服务端把它收窄到提交者 + 管理员），不是加了个标签。 -->
        <section class="fb-section">
          <div class="t-eyebrow mb-1">安全问题</div>
          <div class="t-meta mb-2">
            标成安全问题之后，这条对同事不可见，只有提交者和你这个管理员能看到；读的人打开它只会看到「打不开」。
          </div>
          <v-switch
            :model-value="item.security"
            color="primary"
            density="compact"
            hide-details
            label="标成安全问题"
            @update:model-value="(v: unknown) => store.setSecurity(item!.id, !!v)"
          />
        </section>

        <section class="fb-section">
          <div class="t-eyebrow mb-1">内部备注</div>
          <div class="t-meta mb-2">只有管理员看得到，提交者看不到。加进去就留下，不能改也不能删。</div>

          <div v-for="note in item.notes" :key="note.id" class="fb-note">
            <!-- 备注恒为真人写的，所以这里不传 `is-agent`：管理端的五条写路由都先过
                 `_require_admin`，而它第一步就是拒绝 agent 身份（agent 不能执行管理
                 动作）。这不是「一般是人」，是链路上没有 agent 能写进来的口子。 -->
            <div class="t-meta d-flex align-center ga-2">
              <FeedbackAuthorAvatar :handle="note.author_handle" :size="20" />
              <span>{{ note.author_handle }} · {{ relTime(note.created_at) }}</span>
            </div>
            <p class="t-body fb-text">{{ note.body }}</p>
          </div>
          <div v-if="!item.notes.length" class="t-meta mb-2">还没有备注</div>

          <v-textarea
            v-model="noteDraft"
            class="mt-2"
            autocomplete="off"
            density="compact"
            rows="3"
            hide-details
            placeholder="写点什么给下一个接手的人"
          />
          <div class="d-flex align-center ga-2 mt-2">
            <v-btn size="small" variant="outlined" color="secondary" :disabled="!noteDraft.trim()" @click="addNote">
              加一条备注
            </v-btn>
          </div>
        </section>

        <v-alert v-if="store.error" type="error" density="compact" variant="tonal" class="mt-4">
          {{ store.error }}
        </v-alert>

        <!-- 这里**故意**没有「转为公开」的按钮。理由见文件开头。 -->
      </div>

      <div v-else-if="store.detailLoading" class="fb-admin-drawer__body t-meta">加载中…</div>
      <div v-else class="fb-admin-drawer__body t-meta">{{ store.error ?? '这条反馈打不开' }}</div>
    </div>
  </v-navigation-drawer>
</template>

<style scoped>
.fb-admin-drawer__inner {
  display: flex;
  flex-direction: column;
  height: 100%;
}
.fb-admin-drawer__head {
  display: flex;
  flex: none;
  align-items: flex-start;
  gap: 8px;
  /* 左边 24 和下面 body 的 padding 对齐：标题和正文同一个左边界。 */
  padding: 16px 8px 16px 24px;
  border-bottom: 1px solid var(--line);
}
.min-width-0 {
  min-width: 0;
}
.fb-admin-drawer__body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 24px;
}
.fb-admin-drawer__actions {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 16px;
}
.fb-section + .fb-section {
  margin-top: 24px;
  padding-top: 24px;
  border-top: 1px solid var(--line);
}
.fb-sub {
  margin: 8px 0 4px;
  font-size: 12px;
  color: var(--muted);
}
/* 用户写的正文是**多段**的（换行要保留）。 */
.fb-text {
  white-space: pre-wrap;
  margin-bottom: 0;
}
.fb-pre {
  margin: 0;
  padding: 8px 10px;
  border-radius: var(--radius-sm);
  background: var(--fill);
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: 1.6;
  color: var(--text);
  white-space: pre-wrap;
  word-break: break-word;
}
.fb-note + .fb-note {
  margin-top: 10px;
}
.fb-note {
  padding: 8px 10px;
  border-radius: var(--radius-sm);
  background: var(--fill);
}
.fb-kv {
  display: flex;
  gap: 10px;
  margin-bottom: 4px;
}
.fb-code {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--text);
}
</style>
