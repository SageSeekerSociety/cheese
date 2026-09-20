<script setup lang="ts">
import type { FeedbackKind } from '@/cx_types'

import { computed } from 'vue'
import { useDisplay } from 'vuetify'

import { KIND_LABEL } from '@/lib/feedbackMeta'
import { useFeedbackStore } from '@/stores/feedback'

// 提交反馈的右侧抽屉。
//
// 它只有一份，两个入口：反馈中心的「+ 提交反馈」，和会话里 Agent 卡片上的「提交
// 反馈」（后者的 `draft.proposal` 有值，走的是「发送那张提案卡」那条路）。两处各写
// 一遍的话，「可见范围」这种后来才加的字段必然只会加进其中一份。
//
// 需求里有一条很容易做错：**附件和日志只提示风险，不强制改成私密**。所以那句提示
// 是一个 `v-alert`，不是一个自动把单选框拨到「私密」的 watch —— 公开还是私密是
// 提交者的判断，界面可以提醒他一次，不能替他决定。
//
// 两处还没接的东西，写在界面上而不是藏着：
//   * **附件不随反馈上传**（后端还没有附件字段）。所以文件选择是灰的，并说清原因 ——
//     一个能点、点了什么都不发生的按钮比一个灰按钮更让人困惑。
//   * **「附带现场」只在从 Agent 卡片来时才有意义**：那三段会话信息是卡片带来的。
//     自己从头填的人没有会话可附带，所以那个勾选框对他不出现。
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

/** 词表来自服务端；meta 还没到时用这三个 —— 它们是 `FeedbackKind` 的全部取值，
 *  本地这一份只在第一帧生效（见 lib/feedbackMeta.ts 的同一套理由）。 */
const kinds = computed<FeedbackKind[]>(() => store.meta?.kinds ?? ['bug', 'suggestion', 'other'])

/** 从提案卡打开的那条：作者是提案的 agent，提交者是我，所以两个入口的说法不一样。 */
const fromProposal = computed(() => !!store.draft.proposal)
const canSubmit = computed(() => store.draft.title.trim().length > 0 && !store.submitting)

async function submit() {
  const id = await store.submit()
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
        <span class="t-title">{{ fromProposal ? '发送这条反馈' : '提交反馈' }}</span>
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
          <div class="t-body">
            这条反馈的作者会记成发现它的 AI 队友，提交人记成你 —— 两个都记，所以「芝士
            提的东西有多少真的发出去了」答得出来。
          </div>
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
        <div class="d-flex align-center flex-wrap ga-2 mb-3">
          <v-btn variant="outlined" color="secondary" size="small" prepend-icon="mdi-paperclip" disabled>
            选择文件
          </v-btn>
          <span class="t-meta">上传还没接：附件字段后端还没有，这一版只能提交文字和现场信息。</span>
        </div>

        <!-- 「附带现场」只在从 Agent 卡片进来时出现：那三段（发生了什么 / 复现 / 证据
             加会话 ID 与环境）是卡片带来的，自己从头填的人没有现场可附带。 -->
        <template v-if="store.draft.fromAgent">
          <v-checkbox
            v-model="store.draft.attachContext"
            label="附带芝士发来的现场（发生了什么 / 复现步骤 / 证据 / 会话与环境）"
            hide-details
            class="mb-2"
          />
          <v-alert v-if="store.draft.attachContext" type="warning" density="compact" variant="tonal" class="mb-4">
            现场里可能包含你的代码片段、文件路径或对话内容。选「私密」时只有你和平台管理员能看到它们。
          </v-alert>
        </template>

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
                <div class="fb-radio__hint">只有你和管理员可见，不进公开列表、不能被支持</div>
              </div>
            </template>
          </v-radio>
        </v-radio-group>
        <div class="t-meta">这一条由你决定，而且只能决定这一次 —— 提完之后连管理员也没有把它改成公开的入口。</div>

        <!-- 后端的原话。412 那种「别再点了」也走这里：把服务端说过的话照抄一遍，
             比在客户端另编一句「操作失败」有用得多。 -->
        <v-alert v-if="store.error" type="error" density="compact" variant="tonal" class="mt-4">
          {{ store.error }}
        </v-alert>
      </div>

      <div class="fb-drawer__foot">
        <v-btn variant="text" color="secondary" :disabled="store.submitting" @click="store.closeSubmit()"> 取消 </v-btn>
        <v-spacer />
        <v-btn color="primary" :loading="store.submitting" :disabled="!canSubmit" @click="submit">
          {{ fromProposal ? '发送' : '提交反馈' }}
        </v-btn>
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
