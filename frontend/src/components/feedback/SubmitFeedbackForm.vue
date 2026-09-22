<script setup lang="ts">
import type { FeedbackKind } from '@/cx_types'

import { computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'

import { KIND_LABEL } from '@/lib/feedbackMeta'
import { cleanTags, EXPECTATION_KINDS, REPRO_KINDS, useFeedbackStore } from '@/stores/feedback'

// 提交反馈的**那一份表单**。它有两个壳，字段只有这一份：
//
//   * 页面壳 `views/feedback/FeedbackSubmitPage.vue`（`/feedback/new`）—— 反馈中心
//     和「我的反馈」两个入口走它。一行字要填半天的时候，页面比浮层少一层「我在哪」
//     的疑问，刷新之后人也还停在表单上（路由就是状态，而抽屉/弹窗刷新后会关掉，草稿
//     虽在盘上、界面却没了）。
//   * 对话框壳 `SubmitFeedbackDialog.vue` —— 会话里那张 agent 提案卡走它。**只有它
//     不跳页**：从对话里跳走会把「我刚看到的那张卡」留在身后，而卡片提交完要就地翻
//     成一张凭证（`AgentFeedbackCard` 的 `submitted` 是组件内的 ref，跳页必丢）。
//
// 两个壳各写一遍的话，「可见范围」这种后来才加的字段必然只会加进其中一份。
//
// ## 必填只有两栏，这是算过的
//
// **标题 + 说明**。以前的表单只要求标题，于是一条「登录坏了」不带任何正文也能提交
// —— 那既是「大部分都是选填」的观感来源，也是「提交上来的信息不够」的来源。说明是
// 人人都答得出的那一栏（谁撞上谁说得清发生了什么），把它设成必填不会把谁挡在门外。
//
// **「怎么重现」保持选填**，这是上一版就定下的取舍，这一版不改：说不清复现步骤但
// 确实撞上了的人，恰好是最该被看见的那一条。把它设成必填，挡掉的正是最该进来的。
//
// 门槛也从「只在标题上」搬到了 store 里（`submit()` 同样查两栏）：按钮 disabled 不是
// 替代品 —— 提交这个动作还有别的调用点。
//
// ## 类型的名字不走词表
//
// 三档类型的**名字**（Bug / 建议 / 其他）来自 `lib/feedbackMeta.KIND_LABEL`，卡片、
// 详情页、管理台读的都是它。这一份新界面再从词表里写一遍同样的三个词，就是同一个事实
// 的第二份拷贝，而那种漂开的表现是「列表里叫建议、表单里叫功能请求」。新加的**句子**
// （每类一句后果说明、必填/选填的分界说明）才进词表。
const props = defineProps<{ shell: 'page' | 'dialog' }>()
const emit = defineEmits<{ (e: 'submitted', id: string): void; (e: 'cancel'): void }>()

const store = useFeedbackStore()
const { t } = useI18n()

/** 词表来自服务端；meta 还没到时用这三个 —— 它们是 `FeedbackKind` 的全部取值。 */
const kinds = computed<FeedbackKind[]>(() => store.meta?.kinds ?? ['bug', 'suggestion', 'other'])

/** 从提案卡打开的那条：作者是提案的 agent、提交者是我，所以两个入口的说法不一样。 */
const fromProposal = computed(() => !!store.draft.proposal)

/** agent 带过来的现场。单独取一份是为了模板里能 `v-if` 之后再读它的三段 —— 在
 *  `store.draft.fromAgent` 那条链上做窄化，模板里读到的类型仍可能是 undefined。 */
const origin = computed(() => store.draft.fromAgent)

/** 手上这份是刚从盘上捞回来的。说出来，而不是静默发生 —— 打开表单看到一段不是自己
 *  刚打的字，不解释一句就像串了别人的内容。 */
const restoredNotice = computed(() => store.draftRestored)

// 按类型出现的两栏。**表单项和请求体问的是同一个问题**（`toCreateBody` 用的就是这两
// 个常量），所以这里也读它们，不在这份文件里再写一遍 `kind === 'bug'`：两处各写一遍
// 的话，改口径时表单和请求体会漂开，而漂开的方向恰好是最难看出来的那一种 —— 屏幕上
// 问了、提交上去却没有。
const askRepro = computed(() => REPRO_KINDS.includes(store.draft.kind))
const askExpectation = computed(() => EXPECTATION_KINDS.includes(store.draft.kind))

/** 提交按钮能不能按。查的是**同一个门槛**，见文件头。 */
const canSubmit = computed(() => !!store.draft.title.trim() && !!store.draft.body.trim() && !store.submitting)

/** 说明那一栏的标题、占位语和类型说明**跟着类型走**。三份都写成**字面量的表**，不拼键：
 *  i18n 的闸门（`src/i18n/catalog.spec.ts`）是照着源码里的字面量认「这个键有人用」的，
 *  拼出来的键既不会被算作一次调用，真正那三个叶子又会被判成「没有任何文件引用」——
 *  一边报「键不存在」、一边报「没人用」，两句话都不指向真正的原因。
 *
 *  表值写成**函数**而不是字符串：`t()` 要在求值的那一刻读当前语言。写成常量会在 setup
 *  时求值一次，之后切语言这一栏就不跟着变了（而语言是可以在这一页上切的）。 */
const BODY_COPY: Record<FeedbackKind, () => { label: string; placeholder: string }> = {
  bug: () => ({
    label: t('feedback.submit.field.body.bug.label'),
    placeholder: t('feedback.submit.field.body.bug.placeholder'),
  }),
  suggestion: () => ({
    label: t('feedback.submit.field.body.suggestion.label'),
    placeholder: t('feedback.submit.field.body.suggestion.placeholder'),
  }),
  other: () => ({
    label: t('feedback.submit.field.body.other.label'),
    placeholder: t('feedback.submit.field.body.other.placeholder'),
  }),
}
const bodyLabel = computed(() => BODY_COPY[store.draft.kind]().label)
const bodyPlaceholder = computed(() => BODY_COPY[store.draft.kind]().placeholder)

/** 每一类下面那句后果说明，同样是字面量表，理由同上。 */
const KIND_HINT: Record<FeedbackKind, () => string> = {
  bug: () => t('feedback.submit.kind.hint.bug'),
  suggestion: () => t('feedback.submit.kind.hint.suggestion'),
  other: () => t('feedback.submit.kind.hint.other'),
}
const kindHint = computed(() => KIND_HINT[store.draft.kind]())

/** 标签候选：**从已经加载到的那两份列表里**出现的标签汇总。数据现成，不引新依赖、
 *  也不为它加一个后端接口 —— 候选只是省打字，拿不到候选时这个输入框照样能用。
 *  已经填在表单里的那些要排掉，否则选完一次它还会再提一次同一个词。 */
const tagSuggestions = computed(() => {
  const seen = new Set<string>()
  for (const item of [...store.items, ...store.mineItems]) {
    for (const tag of item.tags) seen.add(tag)
  }
  for (const tag of store.draft.tags) seen.delete(tag)
  return [...seen].sort()
})

/** v-combobox 交给我们的是一串用户输入（含空白、重复、超上限的）。规范化在 store 里
 *  （`cleanTags`），因为它同时也是 `toCreateBody` 用的那一个 —— 输入框先收一遍只是为了
 *  当场去掉重复的 chip，存进草稿的必须和发出去的是同一个形状。 */
function setTags(value: string[]) {
  store.draft.tags = cleanTags(value)
  store.touchDraft()
}

/** 「丢弃草稿」——盘上两份槽位一起抹掉，见 store 里那个同名 action。 */
function discardDraft() {
  store.discardDraft()
}

async function submit() {
  const id = await store.submit()
  // 失败时**什么都不关**：`store.error` 是服务端的原话，它就在上面那块 alert 里，
  // 而人写的那几百字还在表单上 —— 按一下就能重试。
  if (id) emit('submitted', id)
}

onMounted(() => {
  // 从别处回到这一页（返回来改一下、或者刷新）不该把手上的草稿清掉：`openSubmit`
  // 的分支判断就是这件事（手上这份有内容就接着写，空着才去盘上捞）。页面壳不传
  // preset，所以这里不会发生「整份替换」。
  if (props.shell === 'page') store.openSubmit()
})
</script>

<template>
  <form class="sb-form" :class="`sb-form--${shell}`" @submit.prevent="submit">
    <!-- 从 agent 提案卡进来的那条：先把「这条从哪来」说完，再说字段。以前那三样
         （说明、勾选框、隐私提示）散在表单中段，读起来像三件互不相干的事。 -->
    <section v-if="origin" class="sb-origin">
      <div class="sb-origin__head">
        <v-icon size="16" aria-hidden="true">mdi-robot-outline</v-icon>
        <span class="t-title">{{ t('feedback.submit.agent.title') }}</span>
      </div>
      <p class="sb-origin__note t-body">{{ t('feedback.submit.agent.note') }}</p>
      <dl class="sb-origin__facts">
        <template v-if="origin.whatHappened">
          <dt>{{ t('feedback.submit.agent.whatHappened') }}</dt>
          <dd>{{ origin.whatHappened }}</dd>
        </template>
        <template v-if="origin.repro">
          <dt>{{ t('feedback.submit.agent.repro') }}</dt>
          <dd>{{ origin.repro }}</dd>
        </template>
        <template v-if="origin.evidence">
          <dt>{{ t('feedback.submit.agent.evidence') }}</dt>
          <dd>{{ origin.evidence }}</dd>
        </template>
        <template v-if="origin.sessionId || origin.environment">
          <dt>{{ t('feedback.submit.agent.session') }}</dt>
          <dd>{{ [origin.sessionId, origin.environment].filter(Boolean).join(' · ') }}</dd>
        </template>
      </dl>
      <v-checkbox
        v-model="store.draft.attachContext"
        density="compact"
        hide-details
        :label="t('feedback.submit.agent.attach')"
        @update:model-value="store.touchDraft()"
      />
      <v-alert v-if="store.draft.attachContext" type="warning" density="compact" variant="tonal" class="mt-2">
        {{ t('feedback.submit.agent.attachHint') }}
      </v-alert>
    </section>

    <!-- 恢复提示：草稿被捞回来这件事**说出来**，而不是静默发生 —— 否则打开表单看到
         一段不是自己刚打的字，会以为串了别人的内容。 -->
    <div v-if="restoredNotice" class="sb-restored">
      <v-icon size="16" aria-hidden="true">mdi-history</v-icon>
      <span class="t-meta">{{ t('feedback.submit.draft.restored') }}</span>
      <v-btn variant="text" size="small" color="secondary" @click="discardDraft">
        {{ t('feedback.submit.draft.discard') }}
      </v-btn>
    </div>

    <!-- 类型放最前面：它决定后面问哪几栏。它自己不挡提交（有默认值），所以不进必填那
         一档，但必须排在它门控的字段前面。 -->
    <div class="sb-field">
      <div class="t-eyebrow mb-2">{{ t('feedback.submit.kind.label') }}</div>
      <v-btn-toggle
        v-model="store.draft.kind"
        mandatory
        density="comfortable"
        variant="outlined"
        divided
        @update:model-value="store.touchDraft()"
      >
        <v-btn v-for="kind in kinds" :key="kind" :value="kind" size="small">{{ KIND_LABEL[kind] }}</v-btn>
      </v-btn-toggle>
      <p class="sb-hint t-meta">{{ kindHint }}</p>
    </div>

    <!-- 必填/选填的分界。这一行是整张表单的骨架：以前每一栏平铺在同一屏、只有标题
         算门槛，于是它同时读起来像「什么都没要求」和「一堆都要填」。 -->
    <p class="sb-legend t-meta">{{ t('feedback.submit.requiredLegend') }}</p>

    <div class="sb-field">
      <v-text-field
        v-model="store.draft.title"
        autocomplete="off"
        :label="`${t('feedback.submit.field.title.label')} *`"
        :placeholder="t('feedback.submit.field.title.placeholder')"
        maxlength="300"
        hide-details
        @update:model-value="store.touchDraft()"
      />
    </div>

    <div class="sb-field">
      <v-textarea
        v-model="store.draft.body"
        autocomplete="off"
        :label="`${bodyLabel} *`"
        :placeholder="bodyPlaceholder"
        rows="6"
        hide-details
        @update:model-value="store.touchDraft()"
      />
    </div>

    <hr class="sb-divider" />
    <p class="sb-optional t-meta">{{ t('feedback.submit.optionalDivider') }}</p>

    <!-- 按类型出现的两栏。它们不是「选填的额外信息」，而是把正文里常常混成一段的两件
         事分开：**你原本期待什么**（哪里不对）和**怎么重现**（别人能不能看到同一件
         事）。问错对象的代价最大的是复现 —— 建议类问「怎么重现」是在问一个不存在的东西。
         换类型时已经填过的值**不清掉**（`toCreateBody` 按类型决定带不带），所以选错了
         改回来，刚才写的还在。 -->
    <div v-if="askExpectation" class="sb-field">
      <v-textarea
        v-model="store.draft.expectation"
        autocomplete="off"
        :label="t('feedback.submit.field.expectation.label')"
        :placeholder="t('feedback.submit.field.expectation.placeholder')"
        rows="3"
        hide-details
        @update:model-value="store.touchDraft()"
      />
    </div>

    <div v-if="askRepro" class="sb-field">
      <v-textarea
        v-model="store.draft.repro"
        autocomplete="off"
        :label="t('feedback.submit.field.repro.label')"
        :placeholder="t('feedback.submit.field.repro.placeholder')"
        rows="3"
        hide-details
        @update:model-value="store.touchDraft()"
      />
    </div>

    <!-- 标签。后端一直收（`FeedbackCreate.tags`），卡片也一直在渲染 `item.tags`，
         只是表单以前没做。上限跟着后端（20 条），先在这里截住：写完几百字正文才被
         服务端 422 退回来，是最贵的那种失败。 -->
    <div class="sb-field">
      <v-combobox
        :model-value="store.draft.tags"
        :items="tagSuggestions"
        :label="t('feedback.submit.field.tags.label')"
        :placeholder="t('feedback.submit.field.tags.placeholder')"
        :hint="t('feedback.submit.field.tags.helper')"
        multiple
        chips
        closable-chips
        autocomplete="off"
        persistent-hint
        @update:model-value="setTags($event as string[])"
      />
    </div>

    <!-- 可见范围是一次**决定**，不是一栏「选填」：它有默认值（公开），而且提完之后
         谁也改不了。所以它摆在页脚上方，不进上面那段选填区。 -->
    <div class="sb-field">
      <div class="t-eyebrow mb-2">{{ t('feedback.submit.visibility.label') }}</div>
      <v-radio-group
        v-model="store.draft.visibility"
        hide-details
        class="mb-1"
        @update:model-value="store.touchDraft()"
      >
        <v-radio value="public">
          <template #label>
            <div>
              <div class="sb-radio__title">{{ t('feedback.submit.visibility.public.title') }}</div>
              <div class="sb-radio__hint">{{ t('feedback.submit.visibility.public.hint') }}</div>
            </div>
          </template>
        </v-radio>
        <v-radio value="private">
          <template #label>
            <div>
              <div class="sb-radio__title">{{ t('feedback.submit.visibility.private.title') }}</div>
              <!-- 两种写法，分的是这一条有没有房间来源。`draft.proposal` 有值才走 accept
                   那条路，服务端才解得出 `topic_id`；从反馈中心自己提的一条没有房间，
                   房间那一档对它永远关着。读这句话的人正在决定要不要把敏感内容写进去，
                   说宽了他会白删掉细节。 -->
              <div class="sb-radio__hint">
                {{
                  fromProposal
                    ? t('feedback.submit.visibility.private.hintWithRoom')
                    : t('feedback.submit.visibility.private.hintNoRoom')
                }}
              </div>
            </div>
          </template>
        </v-radio>
      </v-radio-group>
      <p class="t-meta">{{ t('feedback.submit.visibility.once') }}</p>
    </div>

    <!-- 服务端的原话（412 的「已经办完了」之类也走这里）：把服务端说过的话照抄一遍，
         比在客户端另编一句「操作失败」有用得多。 -->
    <v-alert v-if="store.error" type="error" density="compact" variant="tonal" class="mt-4">
      {{ store.error }}
    </v-alert>

    <div class="sb-actions">
      <v-btn variant="text" color="secondary" :disabled="store.submitting" @click="emit('cancel')">
        {{ t('feedback.submit.cancel') }}
      </v-btn>
      <v-spacer />
      <v-btn color="primary" type="submit" :loading="store.submitting" :disabled="!canSubmit">
        {{ fromProposal ? t('feedback.submit.send') : t('feedback.submit.submit') }}
      </v-btn>
    </div>
  </form>
</template>

<style scoped>
/* 一列到底，宽度收在 --content-readable 那一档：这一页要读的是一段一段的话，铺满
   1440px 的输入框每行一百多个字，读起来会串行。 */
.sb-form {
  width: 100%;
  max-width: 720px;
  margin: 0 auto;
}
/* 每一栏之间留的是**标题之外**的那点距离：outlined 的 label 骑在边框上（`translateY(-50%)`
   往上溢出约 8px），所以间距不能只按输入框的高度算，否则两栏的 label 会贴在一起。 */
.sb-field {
  margin-top: 20px;
}
.sb-hint {
  margin: 8px 0 0;
}
.sb-legend {
  margin: 20px 0 0;
}
.sb-divider {
  margin: 28px 0 0;
  border: 0;
  border-top: 1px solid var(--line);
}
.sb-optional {
  margin: 12px 0 0;
}
.sb-restored {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 20px;
  padding: 6px 6px 6px 12px;
  border-radius: var(--radius-md);
  background: var(--fill);
}
/* Agent 带过来的那一份：说明它从哪来的。用 wash 而不是左边的竖条 —— 这一列的规矩
   是「强调靠 wash 底色，左条纹只留给引用块和结构线」。 */
.sb-origin {
  margin-bottom: 24px;
  padding: 12px 14px;
  border-radius: var(--radius-md);
  background: var(--fill);
}
.sb-origin__head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
}
.sb-origin__note {
  margin: 0 0 8px;
}
/* 现场那三段用 dl：它们是「字段名 + 值」，不是正文。 */
.sb-origin__facts {
  margin: 0 0 8px;
  font-size: 12px;
}
.sb-origin__facts dt {
  margin-top: 6px;
  color: var(--muted);
}
.sb-origin__facts dd {
  margin: 0;
  color: var(--ink);
  white-space: pre-wrap;
}
.sb-radio__title {
  font-size: 13px;
  color: var(--ink);
}
/* 提示文字用 --muted 而不是 --faint：--faint 在它最好的底色上也只有 4.08:1，够不着
   14px 正文要的 4.5。 */
.sb-radio__hint {
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--muted);
}
.sb-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 28px;
}
/* 页面壳：操作条黏在底部 —— 这张表单长到一屏装不下，而「提交」是填完之后唯一要按的
   那一个。黏住之后它一直在，人不必先滚到底才知道能不能交。 */
.sb-form--page .sb-actions {
  position: sticky;
  bottom: 0;
  margin-top: 24px;
  padding: 12px 0 calc(12px + env(safe-area-inset-bottom));
  background: var(--canvas);
  border-top: 1px solid var(--line);
}
/* 对话框壳：操作条在原地，对话框自己会滚。 */
.sb-form--dialog .sb-actions {
  margin-top: 24px;
}
</style>
