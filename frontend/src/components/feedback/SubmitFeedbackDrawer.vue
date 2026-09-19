<script setup lang="ts">
import type { FeedbackKind } from '@/lib/feedbackMock'

import { computed, ref } from 'vue'
import { useDisplay } from 'vuetify'

import { KIND_LABEL } from '@/lib/feedbackMock'
import { useFeedbackStore } from '@/stores/feedback'

// 提交反馈的右侧抽屉。
//
// 它只有一份，两个入口：反馈中心的「+ 提交反馈」，和会话里 Agent 卡片上的「提交
// 反馈」（后者会带上 What happened / Repro / Evidence）。两处各写一遍的话，
// 「可见范围」这种后来才加的字段必然只会加进其中一份。
//
// 需求里有一条很容易做错：**附件和日志只提示风险，不强制改成私密**。所以那句提示
// 是一个 `v-alert`，不是一个自动把单选框拨到「私密」的 watch —— 公开还是私密是
// 提交者的判断，界面可以提醒他一次，不能替他决定。
const emit = defineEmits<{ (e: 'submitted', id: string): void }>()

const store = useFeedbackStore()
const { xs, width: viewportWidth } = useDisplay()

// 宽度必须是**数字**，不能给 '100%'。VNavigationDrawer 把它当成
// `Number(props.width)` 收下，'100%' 会变成 NaN，而 NaN 一路走到 layoutItemStyles
// 里的 `translateX(${...}px)` —— 那不是合法的 CSS，浏览器把整条 transform 丢掉：
// 抽屉再也移不出屏幕，「关着」的抽屉就整屏压在页面上，底栏正好盖住输入框那一行，
// 点击全被它接走。桌面宽度原本就是数字 460，所以这个坑只在窄屏露头。
//
// 给视口宽度的像素值而不是留空：留空会退回 Vuetify 默认的 256px，窄屏上就成了一条
// 盖不住全屏、但也不该出现的缝。
const drawerWidth = computed(() => (xs.value ? viewportWidth.value : 460))
const kinds: FeedbackKind[] = ['bug', 'suggestion', 'other']

const fileInput = ref<HTMLInputElement | null>(null)
const fileInputRef = (el: unknown) => {
  fileInput.value = (el as HTMLInputElement) ?? null
}

/** 选择了附件、或者勾了「附带会话信息」时才提示。 */
const showRiskHint = computed(() => store.draft.attachments.length > 0 || store.draft.attachLogs)
const canSubmit = computed(() => store.draft.title.trim().length > 0)

function onPickFiles(event: Event) {
  const input = event.target as HTMLInputElement
  for (const file of Array.from(input.files ?? [])) store.addAttachment(file.name)
  // 清空 value：选同一个文件两次时 change 不会再触发，不清就等于第二次点了没反应。
  input.value = ''
}

function submit() {
  const id = store.submit()
  if (id) emit('submitted', id)
}
</script>

<template>
  <v-navigation-drawer
    :model-value="store.submitOpen"
    temporary
    location="right"
    :width="drawerWidth"
    class="fb-drawer"
    @update:model-value="(open: boolean) => !open && store.closeSubmit()"
  >
    <div class="fb-drawer__inner">
      <div class="fb-drawer__head">
        <span class="t-title">提交反馈</span>
        <v-spacer />
        <v-btn icon size="small" variant="text" aria-label="关闭" @click="store.closeSubmit()">
          <v-icon size="18">mdi-close</v-icon>
        </v-btn>
      </div>

      <div class="fb-drawer__body">
        <div v-if="store.draft.fromAgent" class="fb-from-agent mb-4">
          <div class="d-flex align-center ga-2 mb-1">
            <v-icon size="16">mdi-robot-outline</v-icon>
            <span class="t-title">来自芝士的排查</span>
          </div>
          <div class="t-body">现场信息会随这份反馈一起提交，你可以在下面补充说明。</div>
        </div>

        <div class="t-eyebrow mb-2">类型</div>
        <v-btn-toggle
          v-model="store.draft.kind"
          mandatory
          density="comfortable"
          variant="outlined"
          divided
          class="mb-4"
        >
          <v-btn v-for="kind in kinds" :key="kind" :value="kind" size="small">{{ KIND_LABEL[kind] }}</v-btn>
        </v-btn-toggle>

        <v-text-field
          v-model="store.draft.title"
          autocomplete="off"
          label="标题"
          placeholder="一句话说清发生了什么"
          hide-details
          class="mb-4"
        />

        <v-textarea
          v-model="store.draft.body"
          autocomplete="off"
          label="描述"
          placeholder="你做了什么、期望发生什么、实际发生了什么"
          rows="5"
          hide-details
          class="mb-4"
        />

        <div class="t-eyebrow mb-2">附件</div>
        <div class="d-flex align-center flex-wrap ga-2 mb-2">
          <v-btn
            variant="outlined"
            color="secondary"
            size="small"
            prepend-icon="mdi-paperclip"
            @click="fileInput?.click()"
          >
            选择文件
          </v-btn>
          <span class="t-meta">截图、日志、导出文件（原型：只记录文件名，不会真的上传）</span>
        </div>
        <!-- `:ref` 不是 `ref`：这里是函数式 ref（拿到元素就存进 fileInput）。
             写成静态的 `ref="fileInputRef"` 时 Vue 会警告「used on a non-ref value,
             will not work in the production build」——开发时看着能点开文件选择框，
             构建之后 fileInput 永远是 null，「选择文件」就成了死按钮。 -->
        <input :ref="fileInputRef" type="file" multiple class="visually-hidden" @change="onPickFiles" />
        <div v-if="store.draft.attachments.length" class="d-flex flex-wrap ga-2 mb-3">
          <v-chip
            v-for="name in store.draft.attachments"
            :key="name"
            size="small"
            variant="tonal"
            closable
            @click:close="store.removeAttachment(name)"
          >
            <v-icon start size="14">mdi-file-outline</v-icon>{{ name }}
          </v-chip>
        </div>

        <v-checkbox v-model="store.draft.attachLogs" label="附带当前会话的日志与环境信息" hide-details class="mb-2" />

        <v-alert v-if="showRiskHint" type="warning" density="compact" variant="tonal" class="mb-4">
          附件和日志里可能包含你的代码、文件路径或对话内容。选「私密」时只有管理员能看到它们。
        </v-alert>

        <div class="t-eyebrow mb-2">可见范围</div>
        <v-radio-group v-model="store.draft.visibility" hide-details class="mb-2">
          <v-radio value="public">
            <template #label>
              <div>
                <div class="fb-radio__title">公开</div>
                <div class="fb-radio__hint">所有用户可见，可以被支持、被评论</div>
              </div>
            </template>
          </v-radio>
          <v-radio value="private">
            <template #label>
              <div>
                <div class="fb-radio__title">私密</div>
                <div class="fb-radio__hint">仅管理员可见</div>
              </div>
            </template>
          </v-radio>
        </v-radio-group>
        <div class="t-meta">这一条由你决定。选了附件或日志不等于必须选私密。</div>
      </div>

      <div class="fb-drawer__foot">
        <v-btn variant="text" color="secondary" @click="store.closeSubmit()">取消</v-btn>
        <v-spacer />
        <v-btn color="primary" :disabled="!canSubmit" @click="submit">提交反馈</v-btn>
      </div>
    </div>
  </v-navigation-drawer>
</template>

<style scoped>
.fb-drawer__inner {
  display: flex;
  flex-direction: column;
  height: 100%;
}
.fb-drawer__head {
  display: flex;
  flex: none;
  align-items: center;
  /* 左边 24 和下面 body 的 padding 对齐：标题和正文同一个左边界。 */
  padding: 16px 8px 16px 24px;
  border-bottom: 1px solid var(--line);
}
.fb-drawer__body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 24px;
}
.fb-drawer__foot {
  display: flex;
  flex: none;
  align-items: center;
  gap: 8px;
  padding: 16px 24px;
  border-top: 1px solid var(--line);
}
/* Agent 带过来的那一份：说明它从哪来的。用 wash 而不是左边的竖条 —— 这一列的
   规矩是「强调靠 wash 底色，左条纹只留给引用块和结构线」。 */
.fb-from-agent {
  padding: 10px 12px;
  border-radius: var(--radius-md);
  background: var(--fill);
}
.fb-radio__title {
  font-size: 13px;
  color: var(--ink);
}
/* 提示文字用 --muted 而不是 --faint：--faint 在浅色主题下只有 2.6:1 的对比度
   （docs/design-system.md §2.6），那是留给时间戳、编号这类可有可无的信息的，
   用来写「所有用户可见，可以被支持、被评论」读不清。 */
.fb-radio__hint {
  font-size: 12px;
  color: var(--muted);
}
</style>
