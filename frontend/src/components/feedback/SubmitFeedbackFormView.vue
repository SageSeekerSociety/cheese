<script lang="ts">
// 这一件的 props 单独立个名字并导出：`views/feedback/FeedbackSubmitPageView.vue` 把它
// 整个转发一遍（那边没有自己的画面），两边共用这一份类型，就不用抄第二遍。类型导出不
// 构成依赖，那一件仍然是 A 级。
//
// 用到的 `FeedbackDraft` / `FeedbackKind` 从下面那个块的 import 来 —— 两个块编译后是
// 同一个模块。
export interface SubmitFeedbackFormViewProps {
  /** 壳：页面还是对话框。只影响操作条黏不黏底。 */
  shell: 'page' | 'dialog'
  /** 正在填的这一份内容。**只读**：视图改任何一栏都发 `patch`。 */
  draft: FeedbackDraft
  /** 类型候选。词表来自服务端，容器兜了默认值。 */
  kinds: FeedbackKind[]
  /** 手上这份是刚从盘上捞回来的。 */
  restoredNotice: boolean
  /** 按类型出现的那两栏：口径在容器里（和请求体用的是同一份常量）。 */
  askRepro: boolean
  askExpectation: boolean
  /** 标签候选：容器从已经加载到的那两份列表里汇总来的。 */
  tagSuggestions: string[]
  /** 提交请求在飞。 */
  submitting: boolean
  /** 服务端的原话（412 的「已经办完了」之类也走这里）。 */
  error: string | null
  /** 提交按钮能不能按。门槛在 store 里，容器算好了传下来。 */
  canSubmit: boolean
}
</script>

<script setup lang="ts">
import type { FeedbackKind } from '@/cx_types'
import type { FeedbackDraft } from '@/stores/feedback'

import { computed, ref } from 'vue'

import { kindLabel } from './feedbackLabels'

import BaseButton from '@/components/base/BaseButton.vue'
import BaseField from '@/components/base/BaseField.vue'
import { t } from '@/i18n'

// 提交反馈那张表单的**画面**（不是取数的那一半）—— **只吃 props、只往上发事件**。
//
// 它和 `SubmitFeedbackForm.vue` 是一对：那一半接反馈 store（草稿的读、写、落盘、提交），
// 这一半只画。分开的理由是场景棘轮：这一张表单会被**页面**当头等公民渲染，而页面自己
// 必须只吃 props 和事件 —— 表单里那些「谁能碰 store」不该把整页拖进取数的那一档。
//
// 草稿是**只读**进来的（`draft`）。改了哪一栏就发 `patch`，由外面那一半写回 store、
// 顺手落盘；标签走 `add-tag` / `remove-tag`（规范化和上限在那一半，和请求体用的是同一
// 份 `cleanTags`）。**这一层不发请求、不碰路由、不读 store**，所以它可以被单独挂起来看。
//
// ## 必填只有两栏，这是算过的
//
// **标题 + 说明**。以前只要求标题，于是一条「登录坏了」不带任何正文也能提交 —— 那既是
// 「大部分都是选填」的观感来源，也是「提交上来的信息不够」的来源。说明是人人答得出的
// 那一栏，设成必填不会把谁挡在门外。**「怎么重现」保持选填**：说不清复现步骤但确实
// 撞上了的人，恰好是最该被看见的那一条。
//
// 这一层只画那颗必填星号；**门槛在 store 里**（`submit()` 同样查两栏）—— 按钮 disabled
// 不是替代品，提交这个动作还有别的调用点。
//
// ## 控件不走 Vuetify 的输入框
//
// 这一页是**平台自己的表单**，不是嵌进平台的一个 Vuetify 表单：outlined 的
// `v-text-field` 会把 label 骑在边框上、把 helper 挤到边框底下，和反馈中心列表、详情页
// 那一套「标签在上、输入框在下、提示在再下面」的读法对不上。所以输入框、类型、标签、
// 可见范围都用令牌画：`--line` 一圈、`--radius-md` 收角、`--focus-ring` 做焦点环（和
// `AdminQueuePage` 的搜索框同一套）。**行为一点没动**：值绑在传进来的 `draft` 上、改一
// 下就发 `patch`（落盘在外面那一半），门槛还在 store 里算。
//
// ## 类型的名字不走词表
//
// 三档类型的**名字**（Bug / 建议 / 其他）来自 `feedbackLabels.kindLabel()`（i18n 的
// `feedback.kind.*`，卡片、详情页、管理台读的也是它）。这一份界面再写一遍同样的三个词，
// 就是同一个事实的第二份拷贝，而漂开的表现是「列表里叫建议、表单里叫功能请求」。新加的
// **句子**（每类一句后果说明、必填/选填的分界说明）才进词表。
//
// 标题上限。以前只写在输入框的 maxlength 上，粘一段长标题进来会被悄悄截掉；现在同一个
// 数字同时喂给 maxlength 和计数器。
const TITLE_MAX = 300

/** 草稿里由这一层改得动的那几栏。别的（提案卡带来的现场之类）不经过这里。 */
type FormField = 'kind' | 'title' | 'body' | 'repro' | 'expectation' | 'visibility' | 'attachContext'

defineOptions({ name: 'SubmitFeedbackFormView' })

const props = defineProps<SubmitFeedbackFormViewProps>()

const emit = defineEmits<{
  /** 草稿的某几栏改了。写回 store、落盘都在外面那一半。 */
  patch: [values: Partial<Pick<FeedbackDraft, FormField>>]
  /** 加一个标签（规范化、去重、上限在外面那一半）。 */
  'add-tag': [tag: string]
  /** 去掉一个标签。 */
  'remove-tag': [tag: string]
  /** 「丢弃草稿」：把盘上那份抹掉。 */
  'discard-draft': []
  /** 提交。请求由外面那一半发，成了它会往上发 `submitted`。 */
  submit: []
  /** 不填了。 */
  cancel: []
}>()

/** 从提案卡打开的那条：作者是提案的 agent、提交者是我，所以两个入口的说法不一样。 */
const fromProposal = computed(() => !!props.draft.proposal)

/** agent 带过来的现场。单独取一份是为了模板里能 `v-if` 之后再读它的三段 —— 在
 *  `props.draft.fromAgent` 那条链上做窄化，模板里读到的类型仍可能是 undefined。 */
const origin = computed(() => props.draft.fromAgent)

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
const bodyLabel = computed(() => BODY_COPY[props.draft.kind]().label)
const bodyPlaceholder = computed(() => BODY_COPY[props.draft.kind]().placeholder)

/** 每一类下面那句后果说明，同样是字面量表，理由同上。 */
const KIND_HINT: Record<FeedbackKind, () => string> = {
  bug: () => t('feedback.submit.kind.hint.bug'),
  suggestion: () => t('feedback.submit.kind.hint.suggestion'),
  other: () => t('feedback.submit.kind.hint.other'),
}
const kindHint = computed(() => KIND_HINT[props.draft.kind]())

/** 类型那一排按钮就地改草稿。落盘在外面那一半（`patch` 一到就 touch）。 */
function setKind(value: FeedbackKind) {
  emit('patch', { kind: value })
}

// 草稿那几栏的读写口子：读的是 props，写的时候往上报一次。写成 `computed` 的
// get/set 是为了模板里还能照直 `v-model`，而值的来去都留在这一层的边界上。
const title = computed({ get: () => props.draft.title, set: (v: string) => emit('patch', { title: v }) })
const body = computed({ get: () => props.draft.body, set: (v: string) => emit('patch', { body: v }) })
const repro = computed({ get: () => props.draft.repro, set: (v: string) => emit('patch', { repro: v }) })
const expectation = computed({
  get: () => props.draft.expectation,
  set: (v: string) => emit('patch', { expectation: v }),
})
const visibility = computed({
  get: () => props.draft.visibility,
  set: (v: FeedbackDraft['visibility']) => emit('patch', { visibility: v }),
})
const attachContext = computed({
  get: () => props.draft.attachContext,
  set: (v: boolean) => emit('patch', { attachContext: v }),
})

/** 正在输入的那一个标签。它**不进草稿**：半截的词不是标签，写进草稿的话刷新回来会
 *  多出一个「登」。 */
const tagDraft = ref('')

/** 输入框有焦点（画焦点环用）。`:focus-within` 在带 chip 的容器上不好使 —— 焦点在
 *  里面那个 input 上，容器要的是「自己看起来被聚焦了」。 */
const tagFocused = ref(false)

function commitTag() {
  const raw = tagDraft.value
  if (!raw.trim()) {
    tagDraft.value = ''
    return
  }
  tagDraft.value = ''
  emit('add-tag', raw)
}

/** 输入框已经空着的时候按退格，删掉最后一个芯片 —— 芯片输入框的老规矩，没有它，
 *  想删掉刚打错的那个词只能用鼠标去点那个 16px 的叉。 */
function onTagBackspace() {
  if (tagDraft.value || !props.draft.tags.length) return
  emit('remove-tag', props.draft.tags[props.draft.tags.length - 1])
}

/** 回车和逗号都是「这一个标签写完了」。逗号不能写成 `@keydown.comma` —— Vue 的按键
 *  修饰符里没有 `comma` 这个别名（自带的只有 enter/tab/delete/esc/space 和方向键），
 *  写出来是一条不生效的规则，eslint 也会当场报出来。所以按 `event.key` 自己判。
 *  两个键都要 `preventDefault`：不然回车会提交表单、逗号会跟着一起进输入框。 */
function onTagKeydown(event: KeyboardEvent) {
  if (event.key === 'Enter' || event.key === ',') {
    event.preventDefault()
    commitTag()
    return
  }
  if (event.key === 'Backspace') onTagBackspace()
}
</script>

<template>
  <form class="sb-form" :class="`sb-form--${shell}`" @submit.prevent="emit('submit')">
    <!-- Agent 带过来的那条：先把「这条从哪来」说完，再说字段。以前那三样（说明、勾选
         框、隐私提示）散在表单中段，读起来像三件互不相干的事。 -->
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
      <label class="sb-check">
        <input v-model="attachContext" type="checkbox" class="sb-check__box" />
        <span>{{ t('feedback.submit.agent.attach') }}</span>
      </label>
      <p v-if="attachContext" class="sb-note sb-note--warn">
        {{ t('feedback.submit.agent.attachHint') }}
      </p>
    </section>

    <!-- 恢复提示：草稿被捞回来这件事**说出来**，而不是静默发生 —— 否则打开表单看到
         一段不是自己刚打的字，会以为串了别人的内容。 -->
    <div v-if="restoredNotice" class="sb-restored">
      <v-icon size="16" aria-hidden="true">mdi-history</v-icon>
      <span class="t-meta-read t-num">{{ t('feedback.submit.draft.restored') }}</span>
      <BaseButton kind="ghost" size="sm" @click="emit('discard-draft')">
        {{ t('feedback.submit.draft.discard') }}
      </BaseButton>
    </div>

    <!-- 类型放最前面：它决定后面问哪几栏。它自己不挡提交（有默认值），所以不进必填那
         一档，但必须排在它门控的字段前面。分段控件（而不是 `v-btn-toggle`）：32px 的
         一条 `--fill` 底槽 + 选中那颗抬到 `--surface`，和 `AdminQueuePage` 的「栏位」
         是同一个控件，两个地方不该长得不一样。 -->
    <div class="sb-field">
      <div id="sb-kind-label" class="sb-label">{{ t('feedback.submit.kind.label') }}</div>
      <div class="sb-seg" role="radiogroup" aria-labelledby="sb-kind-label">
        <button
          v-for="item in kinds"
          :key="item"
          type="button"
          class="sb-seg__item"
          :class="{ 'sb-seg__item--on': draft.kind === item }"
          role="radio"
          :aria-checked="draft.kind === item"
          @click="setKind(item)"
        >
          {{ kindLabel(item) }}
        </button>
      </div>
      <p class="sb-hint t-meta-read">{{ kindHint }}</p>
    </div>

    <!-- 必填/选填的分界。这一行是整张表单的骨架：以前每一栏平铺在同一屏、只有标题
         算门槛，于是它同时读起来像「什么都没要求」和「一堆都要填」。 -->
    <p class="sb-legend t-meta-read">{{ t('feedback.submit.requiredLegend') }}</p>

    <div class="sb-field">
      <label class="sb-label" for="sb-title">
        {{ t('feedback.submit.field.title.label') }}
        <span class="sb-req" aria-hidden="true">*</span>
      </label>
      <BaseField id="sb-title" :counter="{ current: draft.title.length, max: TITLE_MAX }">
        <template #default="{ id, describedby, invalid }">
          <input
            :id="id"
            v-model="title"
            class="sb-input"
            type="text"
            autocomplete="off"
            spellcheck="false"
            :maxlength="TITLE_MAX"
            aria-required="true"
            :aria-describedby="describedby"
            :aria-invalid="invalid"
            :placeholder="t('feedback.submit.field.title.placeholder')"
          />
        </template>
      </BaseField>
    </div>

    <div class="sb-field">
      <label class="sb-label" for="sb-body">
        {{ bodyLabel }}
        <span class="sb-req" aria-hidden="true">*</span>
      </label>
      <textarea
        id="sb-body"
        v-model="body"
        class="sb-textarea"
        rows="6"
        autocomplete="off"
        aria-required="true"
        :placeholder="bodyPlaceholder"
      />
    </div>

    <hr class="sb-divider" />
    <p class="sb-optional t-meta-read">{{ t('feedback.submit.optionalDivider') }}</p>

    <!-- 按类型出现的两栏。它们不是「选填的额外信息」，而是把正文里常常混成一段的两件
         事分开：**你原本期待什么**（哪里不对）和**怎么重现**（别人能不能看到同一件
         事）。问错对象的代价最大的是复现 —— 建议类问「怎么重现」是在问一个不存在的东西。
         换类型时已经填过的值**不清掉**（`toCreateBody` 按类型决定带不带），所以选错了
         改回来，刚才写的还在。 -->
    <div v-if="askExpectation" class="sb-field">
      <label class="sb-label" for="sb-expectation">
        {{ t('feedback.submit.field.expectation.label') }}
      </label>
      <textarea
        id="sb-expectation"
        v-model="expectation"
        class="sb-textarea"
        rows="3"
        autocomplete="off"
        :placeholder="t('feedback.submit.field.expectation.placeholder')"
      />
    </div>

    <div v-if="askRepro" class="sb-field">
      <label class="sb-label" for="sb-repro">{{ t('feedback.submit.field.repro.label') }}</label>
      <textarea
        id="sb-repro"
        v-model="repro"
        class="sb-textarea"
        rows="3"
        autocomplete="off"
        :placeholder="t('feedback.submit.field.repro.placeholder')"
      />
    </div>

    <!-- 标签。后端一直收（`FeedbackCreate.tags`），卡片也一直在渲染 `item.tags`，
         只是表单以前没做。芯片是**手画的**：`v-combobox` 的芯片和下拉用的是 Vuetify
         自己的尺寸和颜色，和这一页别处不一样；这里的输入框和上面的输入框同高同框，
         候选就排在框底下（点一下加一个，不弹层）。上限跟着后端（20 条），先在这一层
         截住：写完几百字正文才被服务端 422 退回来，是最贵的那种失败。 -->
    <div class="sb-field">
      <label class="sb-label" for="sb-tag-input">{{ t('feedback.submit.field.tags.label') }}</label>
      <div class="sb-tags" :class="{ 'sb-tags--focus': tagFocused }">
        <span v-for="tag in draft.tags" :key="tag" class="sb-tag">
          {{ tag }}
          <button
            type="button"
            class="sb-tag__x"
            :aria-label="t('feedback.submit.field.tags.remove', { tag })"
            @click="emit('remove-tag', tag)"
          >
            <v-icon size="12" aria-hidden="true">mdi-close</v-icon>
          </button>
        </span>
        <input
          id="sb-tag-input"
          v-model="tagDraft"
          class="sb-tags__input"
          type="text"
          autocomplete="off"
          spellcheck="false"
          :placeholder="t('feedback.submit.field.tags.placeholder')"
          @focus="tagFocused = true"
          @blur="tagFocused = false"
          @keydown="onTagKeydown"
        />
      </div>
      <div v-if="tagSuggestions.length" class="sb-suggest">
        <span class="sb-suggest__label t-meta-read">{{ t('feedback.submit.field.tags.suggestions') }}</span>
        <button
          v-for="tag in tagSuggestions"
          :key="tag"
          type="button"
          class="sb-suggest__item"
          @click="emit('add-tag', tag)"
        >
          {{ tag }}
        </button>
      </div>
      <p class="sb-hint t-meta-read">{{ t('feedback.submit.field.tags.helper') }}</p>
    </div>

    <!-- 可见范围是一次**决定**，不是一栏「选填」：它有默认值（公开），而且提完之后
         谁也改不了。所以它摆在页脚上方，不进上面那段选填区。两张整块的选项卡而不是
         两个 radio 点：两边的后果不一样长，读的人要在**选之前**读完，所以每一档自己
         占一块、选中那一块抬起来（--surface + 一圈 --line-2），和列表里选中的行同一套。 -->
    <div class="sb-field">
      <div class="sb-label">{{ t('feedback.submit.visibility.label') }}</div>
      <div class="sb-opts">
        <label class="sb-opt" :class="{ 'sb-opt--on': visibility === 'public' }">
          <input v-model="visibility" class="sb-opt__radio" type="radio" name="sb-visibility" value="public" />
          <span class="sb-opt__body">
            <span class="sb-opt__title">{{ t('feedback.submit.visibility.public.title') }}</span>
            <span class="sb-opt__hint">{{ t('feedback.submit.visibility.public.hint') }}</span>
          </span>
        </label>
        <label class="sb-opt" :class="{ 'sb-opt--on': visibility === 'private' }">
          <input v-model="visibility" class="sb-opt__radio" type="radio" name="sb-visibility" value="private" />
          <span class="sb-opt__body">
            <span class="sb-opt__title">{{ t('feedback.submit.visibility.private.title') }}</span>
            <!-- 两种写法，分的是这一条有没有房间来源。`draft.proposal` 有值才走 accept
                 那条路，服务端才解得出 `topic_id`；从反馈中心自己提的一条没有房间，
                 房间那一档对它永远关着。读这句话的人正在决定要不要把敏感内容写进去，
                 说宽了他会白删掉细节。 -->
            <span class="sb-opt__hint">
              {{
                fromProposal
                  ? t('feedback.submit.visibility.private.hintWithRoom')
                  : t('feedback.submit.visibility.private.hintNoRoom')
              }}
            </span>
          </span>
        </label>
      </div>
      <p class="sb-hint t-meta-read">{{ t('feedback.submit.visibility.once') }}</p>
    </div>

    <!-- 服务端的原话（412 的「已经办完了」之类也走这里）：把服务端说过的话照抄一遍，
         比在客户端另编一句「操作失败」有用得多。 -->
    <p v-if="error" class="sb-note sb-note--error">{{ error }}</p>

    <div class="sb-actions">
      <BaseButton kind="ghost" :disabled="submitting" @click="emit('cancel')">
        {{ t('feedback.submit.cancel') }}
      </BaseButton>
      <v-spacer />
      <BaseButton kind="primary" type="submit" :loading="submitting" :disabled="!canSubmit">
        {{ fromProposal ? t('feedback.submit.send') : t('feedback.submit.submit') }}
      </BaseButton>
    </div>
  </form>
</template>

<style scoped>
/* 一列到底，宽度收在 --content-readable 那一档：这一页要读的是一段一段的话，铺满
   1440px 的输入框每行一百多个字，读起来会串行。
   靠左而不居中：页壳的标题靠左，表单居中的话标题会比表单往左突出一截，页面上就有
   两条左沿。 */
.sb-form {
  width: 100%;
  max-width: 720px;
}
/* 每一栏之间留的是**标题之外**的那点距离：标签在上、输入框在下，两栏之间要能一眼
   看出是两个问题，所以间距比输入框自己的高度小、比行距大。 */
.sb-field {
  margin-top: 20px;
}
/* 标签：13px 600 --ink。它和 `.t-eyebrow`（12px 大写间距）不是一档 —— 这是要人读着
   填的东西，不是分区标题。 */
.sb-label {
  display: block;
  margin-bottom: 6px;
  font-size: 13px;
  font-weight: 600;
  line-height: var(--lh-13);
  color: var(--ink);
}
/* 必填的星号。**不进无障碍树**：必填这件事真正的说明是下面那行「带 * 的是必填」，
   而且它同时挂在 `aria-required` 上，读屏报一次就够了。 */
.sb-req {
  color: var(--danger-ink);
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
  margin: 0 0 10px;
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
  overflow-wrap: anywhere;
}
/* 「把这段现场一起提交」。原生 checkbox 自己画：`v-checkbox` 的触控区有 40px 高，
   在这一块 12px 的事实下面会把底边撑出一截空白。 */
.sb-check {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  font-size: 13px;
  line-height: var(--lh-13);
  color: var(--ink);
  cursor: pointer;
}
.sb-check__box {
  flex: none;
  width: 15px;
  height: 15px;
  margin: 2px 0 0;
  accent-color: var(--accent);
  cursor: pointer;
}
/* 整块的说明（现场里可能有什么、服务端的原话）。两种语气同一副骨架：warn 的底是
   `--warn-wash`，error 的是 `--danger-wash`，都配对应的文字色 —— 深色主题下也读得清。 */
.sb-note {
  margin: 8px 0 0;
  padding: 8px 10px;
  border-radius: var(--radius-md);
  font-size: 12px;
  line-height: var(--lh-12);
}
.sb-note--warn {
  background: var(--warn-wash);
  color: var(--warn-ink);
}
.sb-note--error {
  margin-top: 20px;
  background: var(--danger-wash);
  color: var(--danger-ink);
}
/* ---- 输入框：一条线、一个圆角、一圈焦点环 ---- */
.sb-input,
.sb-textarea {
  display: block;
  width: 100%;
  box-sizing: border-box;
  padding: 8px 10px;
  color: var(--ink);
  font-family: inherit;
  font-size: 14px;
  line-height: var(--lh-14);
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
}
.sb-textarea {
  min-height: 72px;
  resize: vertical;
}
.sb-input::placeholder,
.sb-textarea::placeholder {
  color: var(--faint);
}
/* 焦点环跟着全局那一套走（`--focus-ring`）：只换边框色，不再套一层 outline ——
   输入框的框本身就是焦点指示的载体，32px 高的格子里多一圈会顶到邻居。 */
.sb-input:focus,
.sb-textarea:focus {
  border-color: var(--focus-ring);
  outline: none;
}
/* ---- 类型：分段控件 ---- */
.sb-seg {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 32px;
  padding: 0 4px;
  background: var(--fill);
  border-radius: var(--radius-md);
}
.sb-seg__item {
  height: 24px;
  padding: 0 12px;
  color: var(--muted);
  font-family: inherit;
  font-size: 12px;
  font-weight: 600;
  line-height: var(--lh-12);
  white-space: nowrap;
  background: transparent;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition:
    background-color 0.12s ease,
    color 0.12s ease;
}
.sb-seg__item:hover {
  color: var(--text);
}
/* 选中的那颗抬到底色之上（`--surface` + 一圈描边）。中性色 —— 类型是一个位置，
   不是一条告警，琥珀只留给这一页的主操作「提交反馈」。 */
.sb-seg__item--on {
  color: var(--ink);
  background: var(--surface);
  border-color: var(--line);
}
/* ---- 标签：芯片 + 输入 ---- */
.sb-tags {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  min-height: 38px;
  padding: 5px 6px;
  box-sizing: border-box;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
}
.sb-tags--focus {
  border-color: var(--focus-ring);
}
.sb-tag {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  height: 22px;
  padding: 0 4px 0 8px;
  color: var(--muted);
  font-size: 12px;
  line-height: var(--lh-12);
  background: var(--fill);
  border-radius: var(--radius-sm);
}
.sb-tag__x {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  padding: 0;
  color: var(--faint);
  background: transparent;
  border: 0;
  border-radius: var(--radius-sm);
  cursor: pointer;
}
.sb-tag__x:hover {
  color: var(--ink);
  background: var(--fill-2);
}
/* 输入框在芯片后面接着长：它自己不画框（框在外面那层 `.sb-tags` 上），所以光标停
   在最后一个芯片右边，看着就是「接着打」。 */
.sb-tags__input {
  flex: 1 1 120px;
  min-width: 120px;
  height: 26px;
  padding: 0 4px;
  color: var(--ink);
  font-family: inherit;
  font-size: 13px;
  line-height: var(--lh-13);
  background: transparent;
  border: 0;
  outline: none;
}
.sb-tags__input::placeholder {
  color: var(--faint);
}
/* 候选排在输入框底下，点一下加一个 —— 不弹层。候选只是省打字，弹层会挡住上面刚
   写好的正文，而候选值不值得看，人是当场知道的。 */
.sb-suggest {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
}
.sb-suggest__label {
  margin-right: 2px;
}
.sb-suggest__item {
  height: 22px;
  padding: 0 8px;
  color: var(--muted);
  font-family: inherit;
  font-size: 12px;
  line-height: var(--lh-12);
  background: transparent;
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  cursor: pointer;
}
.sb-suggest__item:hover {
  color: var(--ink);
  background: var(--fill);
}
/* ---- 可见范围：两张整块的选项卡 ---- */
.sb-opts {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.sb-opt {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 10px 12px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  cursor: pointer;
}
.sb-opt--on {
  border-color: var(--line-2);
  background: var(--fill);
}
.sb-opt__radio {
  flex: none;
  width: 15px;
  height: 15px;
  margin: 2px 0 0;
  accent-color: var(--accent);
  cursor: pointer;
}
.sb-opt__body {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.sb-opt__title {
  font-size: 13px;
  font-weight: 600;
  line-height: var(--lh-13);
  color: var(--ink);
}
/* 提示文字用 --muted 而不是 --faint：--faint 在它最好的底色上也只有 4.08:1，够不着
   14px 正文要的 4.5。 */
.sb-opt__hint {
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--muted);
  overflow-wrap: anywhere;
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
  background: var(--surface);
  border-top: 1px solid var(--line);
}
/* 对话框壳：操作条在原地，对话框自己会滚。 */
.sb-form--dialog .sb-actions {
  margin-top: 24px;
}
</style>
