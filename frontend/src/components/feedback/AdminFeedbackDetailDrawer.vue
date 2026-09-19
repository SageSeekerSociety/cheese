<script setup lang="ts">
import type { FeedbackItem, FeedbackStatus } from '@/lib/feedbackMock'

import { computed, ref, watch } from 'vue'

import FeedbackStatusChip from './FeedbackStatusChip.vue'

import { ASSIGNEES, KIND_LABEL, PRIORITY_META, SOURCE_LABEL, STATUS_LADDER, STATUS_META } from '@/lib/feedbackMock'
import { relTime } from '@/lib/relTime'
import { useFeedbackStore } from '@/stores/feedback'

// 管理员列表点开之后右侧那一栏。
//
// 需求明写：**不要提供「管理员将私密反馈转公开」的按钮**。这里因此也没有这个动作，
// 而且不是「先不做」——是不该做：公开与否是提交者当场做的决定，管理员能改它就等于
// 那个决定是假的。组件里连一个改 visibility 的入口都没有，是为了将来接后端时不会
// 有人顺手补一个上去。
//
// 内部备注用**本地 draft + 保存按钮**，不是 v-model 直接写 store：备注是管理员写
// 给自己人看的，边打字边存意味着半句话也会进别人看到的记录里。
const props = defineProps<{ item: FeedbackItem | null }>()
const emit = defineEmits<{ (e: 'update:open', value: boolean): void }>()

const store = useFeedbackStore()

const noteDraft = ref('')
const noteSaved = ref(false)

watch(
  () => props.item?.id,
  () => {
    noteDraft.value = props.item?.internalNote ?? ''
    noteSaved.value = false
  },
  { immediate: true }
)

const statusItems = computed(() => STATUS_LADDER.map((s) => ({ title: STATUS_META[s].label, value: s })))
const priorityItems = Object.entries(PRIORITY_META).map(([value, meta]) => ({ title: meta.label, value }))

function saveNote() {
  if (!props.item) return
  store.setInternalNote(props.item.id, noteDraft.value)
  noteSaved.value = true
}
</script>

<template>
  <v-navigation-drawer
    :model-value="!!item"
    temporary
    location="right"
    width="520"
    class="fb-admin-drawer"
    @update:model-value="(open: boolean) => !open && emit('update:open', false)"
  >
    <div v-if="item" class="fb-admin-drawer__inner">
      <div class="fb-admin-drawer__head">
        <div class="min-width-0">
          <div class="t-meta">{{ item.id }} · {{ SOURCE_LABEL[item.source] }}</div>
          <div class="t-title">{{ item.title }}</div>
        </div>
        <v-spacer />
        <v-btn icon size="small" variant="text" aria-label="关闭" @click="emit('update:open', false)">
          <v-icon size="18">mdi-close</v-icon>
        </v-btn>
      </div>

      <div class="fb-admin-drawer__body">
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
              @update:model-value="(v: FeedbackStatus) => store.setStatus(item!.id, v, '管理员')"
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
              @update:model-value="(v: FeedbackItem['priority']) => store.setPriority(item!.id, v)"
            />
          </div>
          <div class="fb-field">
            <div class="t-eyebrow mb-1">指派处理</div>
            <v-select
              :model-value="item.assignee ?? null"
              :items="[{ title: '未指派', value: null }, ...ASSIGNEES.map((h) => ({ title: h, value: h }))]"
              autocomplete="off"
              density="compact"
              hide-details
              @update:model-value="(v: string | null) => store.assign(item!.id, v)"
            />
          </div>
        </div>

        <div class="d-flex align-center flex-wrap ga-2 mb-4">
          <FeedbackStatusChip :status="item.status" />
          <FeedbackStatusChip :priority="item.priority" />
          <span class="chip-neutral">{{ KIND_LABEL[item.kind] }}</span>
          <span v-if="item.security" class="chip-neutral">
            <v-icon size="12">mdi-shield-alert-outline</v-icon>安全
          </span>
        </div>

        <div class="t-meta mb-4">
          {{ item.author }} · {{ relTime(item.createdAt) }} · 支持 {{ item.supports }} · 评论
          {{ item.comments.length }}
        </div>

        <section class="fb-section">
          <div class="t-eyebrow mb-1">用户描述</div>
          <p class="t-body">{{ item.problem }}</p>
          <template v-if="item.why">
            <div class="t-eyebrow mt-3 mb-1">为什么需要</div>
            <p class="t-body">{{ item.why }}</p>
          </template>
          <template v-if="item.expectation">
            <div class="t-eyebrow mt-3 mb-1">期望方案</div>
            <p class="t-body">{{ item.expectation }}</p>
          </template>
        </section>

        <section v-if="item.whatHappened || item.repro || item.evidence" class="fb-section">
          <div class="t-eyebrow mb-1">现场</div>
          <template v-if="item.whatHappened">
            <div class="fb-sub">发生了什么</div>
            <p class="t-body">{{ item.whatHappened }}</p>
          </template>
          <template v-if="item.repro">
            <div class="fb-sub">复现步骤</div>
            <pre class="fb-pre">{{ item.repro }}</pre>
          </template>
          <template v-if="item.evidence">
            <div class="fb-sub">证据</div>
            <p class="t-body">{{ item.evidence }}</p>
          </template>
        </section>

        <section v-if="item.logs" class="fb-section">
          <div class="t-eyebrow mb-1">日志</div>
          <pre class="fb-pre">{{ item.logs }}</pre>
        </section>

        <section v-if="item.sessionId || item.environment" class="fb-section">
          <div class="t-eyebrow mb-1">会话信息</div>
          <div v-if="item.sessionId" class="fb-kv">
            <span class="t-meta">会话</span>
            <code class="fb-code">{{ item.sessionId }}</code>
          </div>
          <div v-if="item.environment" class="fb-kv">
            <span class="t-meta">环境</span>
            <span class="t-body">{{ item.environment }}</span>
          </div>
        </section>

        <section class="fb-section">
          <div class="t-eyebrow mb-1">内部备注</div>
          <div class="t-meta mb-2">只有管理员看得到，提交者看不到。</div>
          <v-textarea
            v-model="noteDraft"
            autocomplete="off"
            density="compact"
            rows="3"
            hide-details
            placeholder="写点什么给下一个接手的人"
          />
          <div class="d-flex align-center ga-2 mt-2">
            <v-btn size="small" variant="outlined" color="secondary" @click="saveNote">保存备注</v-btn>
            <span v-if="noteSaved" class="t-meta">已保存（原型：只存在这个页面里）</span>
          </div>
        </section>

        <!-- 这里**故意**没有「转为公开」的按钮。理由见文件开头。 -->
      </div>
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
