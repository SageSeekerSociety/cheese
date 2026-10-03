<script setup lang="ts">
/**
 * 提问卡 —— **提案组件，尚未接进产品。**
 *
 * 现在的提问是聊天消息 + 下面一排按钮（`RoomMessage.vue` 的 `.ask-row`）：点一下
 * 就发出去、没有「想说别的」的入口、答完只剩一行字。这一张把「需要你拍个板」画成
 * 一件有自己形状的东西：它有题目、有状态、有选项和每个选项的后果，回答可以捎一句
 * 话，答完留下回执。
 *
 * **只加不改**：产品里现有的动作一个都不动，这一张是并排比对用的，不替换任何东西。
 * 接进 `RoomMessage` 与否、怎么接，等验收人看过再说。
 *
 * 样式全部走 `style.css` 的令牌（`--ink` / `--line-2` / `--accent` …）。那张表是
 * 两套主题共用一套名字，所以深色是白拿的：这个组件里没有任何 `--xxx-dark`。
 *
 * 文案直接写中文、没走 i18n：这是提案，先让读的人看懂形状，接进产品时再迁进
 * `zh-CN` / `en` 两张表。
 */
import { computed, ref } from 'vue'

export interface AskOption {
  label: string
  /** 选了会怎么样。顶流的做法是先看后果再点，而不是点完才知道。 */
  consequence?: string
}

type Phase = 'asking' | 'answered' | 'deferred'

const props = withDefaults(
  defineProps<{
    question: string
    options: AskOption[]
    /** 「2 小时了」这种。没有就不画岁数那一段。 */
    age?: string
    /** 只用来填预览里的「谁答的」。 */
    answeredBy?: string
    /** 预览站要能逐格摆出某个时刻，所以起点可以给定。产品里用不到这几样。 */
    initialPhase?: Phase
    initialPicked?: number | null
    initialNote?: string
    initialSubmitted?: { option: string; note: string } | null
  }>(),
  {
    age: '',
    answeredBy: '王长鑫',
    initialPhase: 'asking',
    initialPicked: null,
    initialNote: '',
    initialSubmitted: null,
  }
)

const emit = defineEmits<{
  submit: [payload: { option: string; note: string }]
  defer: []
}>()

const phase = ref<Phase>(props.initialPhase)
const picked = ref<number | null>(props.initialPicked)
const note = ref(props.initialNote)
const submitted = ref<{ option: string; note: string } | null>(props.initialSubmitted)

const canSubmit = computed(() => picked.value !== null)

function submit() {
  if (!canSubmit.value || picked.value === null) return
  const payload = { option: props.options[picked.value].label, note: note.value.trim() }
  submitted.value = payload
  phase.value = 'answered'
  emit('submit', payload)
}

function defer() {
  phase.value = 'deferred'
  emit('defer')
}

function reopen() {
  phase.value = 'asking'
}

/** 自由输入是骑在答案上的那一句，不是旁边另开的一条通道。空着也答得了。 */
const notePlaceholder = '或者写一句别的——选项都不合适时，这里说你真正想要的'
</script>

<template>
  <section class="ask" :class="{ 'ask--answered': phase === 'answered', 'ask--deferred': phase === 'deferred' }">
    <!-- 状态条：一眼看出这是「等你回答」，而不是谁随口说的一句话。 -->
    <header class="ask__status">
      <span class="ask__dot" aria-hidden="true"></span>
      <span class="ask__status-text">{{
        phase === 'asking' ? '等你回答' : phase === 'deferred' ? '你先放着' : '已回答'
      }}</span>
      <span v-if="age && phase !== 'answered'" class="ask__age">{{ age }}</span>
    </header>

    <!-- 题目：短、自包含，不依赖上面的上下文才看得懂。 -->
    <h3 class="ask__title">{{ question }}</h3>

    <!-- 已答：三个时刻里最后一个，答完不消失，留回执。 -->
    <div v-if="phase === 'answered' && submitted" class="ask__receipt">
      <p class="ask__receipt-main">
        <span class="ask__ok" aria-hidden="true">✓</span>
        <strong>{{ answeredBy }}</strong>
        选了「{{ submitted.option }}」
      </p>
      <p v-if="submitted.note" class="ask__receipt-note">捎了一句：{{ submitted.note }}</p>
      <p class="ask__receipt-follow">已交给芝士，它下一轮会接着往下做。</p>
      <button type="button" class="ask__ghost" @click="reopen">改答案</button>
    </div>

    <!-- 稍后处理：放着不等于忘了。标着它在等谁，随时回来。 -->
    <div v-else-if="phase === 'deferred'" class="ask__deferred">
      <p class="ask__deferred-text">先放着了。它还在这儿等你，回来点开就能答。</p>
      <button type="button" class="ask__ghost" @click="reopen">现在答</button>
    </div>

    <template v-else>
      <!-- 选项：点一下只是**选中**，不发出去 —— 手滑能反悔、能换。 -->
      <ul class="ask__options">
        <li v-for="(opt, i) in options" :key="opt.label">
          <button
            type="button"
            class="ask__option"
            :class="{ 'ask__option--on': picked === i }"
            :aria-pressed="picked === i"
            @click="picked = i"
          >
            <span class="ask__radio" aria-hidden="true"></span>
            <span class="ask__option-body">
              <span class="ask__option-label">{{ opt.label }}</span>
              <span v-if="opt.consequence" class="ask__option-consequence">{{ opt.consequence }}</span>
            </span>
          </button>
        </li>
      </ul>

      <!-- 自由输入：跟着这一次回答走。选哪一项都能带一句，也可以不带。 -->
      <input
        v-model="note"
        autocomplete="off"
        class="ask__note"
        type="text"
        :placeholder="notePlaceholder"
        @keydown.enter="submit"
      />

      <footer class="ask__actions">
        <!-- 主操作：这一组里唯一的琥珀（设计规范 §1.6）。没选东西时按不动。 -->
        <button type="button" class="ask__primary" :disabled="!canSubmit" @click="submit">提交回答</button>
        <button type="button" class="ask__later" @click="defer">稍后处理</button>
        <span class="ask__hint">{{ canSubmit ? '点「提交回答」才算答上' : '先选一项' }}</span>
      </footer>
    </template>
  </section>
</template>

<style scoped>
/* 整张卡画在 --surface 上，坐在 --canvas 的房间里。边框只用一根 hairline，
   不加阴影：阴影在这一版设计里留给浮层，卡片靠底色和描边分层。 */
.ask {
  max-width: 460px;
  padding: 14px 16px 12px;
  background: var(--surface);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-lg);
}

.ask--answered,
.ask--deferred {
  background: var(--fill);
}

/* ── 状态条 ────────────────────────────────────────────── */
.ask__status {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 6px;
  font-size: 12px;
  color: var(--faint);
}
.ask__dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--accent);
}
.ask--answered .ask__dot,
.ask--deferred .ask__dot {
  background: var(--faint);
}
.ask__age {
  margin-left: auto;
}

/* ── 题目 ─────────────────────────────────────────────── */
.ask__title {
  margin: 0 0 10px;
  color: var(--ink);
  font-size: 15px;
  font-weight: 600;
  line-height: 1.45;
}

/* ── 选项 ─────────────────────────────────────────────── */
.ask__options {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin: 0 0 10px;
  padding: 0;
  list-style: none;
}
.ask__option {
  display: flex;
  align-items: flex-start;
  gap: 9px;
  width: 100%;
  padding: 9px 12px;
  color: var(--ink);
  font-size: 13px;
  text-align: left;
  cursor: pointer;
  background: var(--surface);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-md);
  transition:
    border-color var(--dur-quick) var(--ease-standard),
    background-color var(--dur-quick) var(--ease-standard);
}
.ask__option:hover {
  border-color: var(--faint);
  background: var(--fill);
}
/* 选中靠描边和底色说，不上琥珀：琥珀留给唯一那颗「提交回答」。 */
.ask__option--on {
  background: var(--line-2);
  border-color: var(--muted);
}
.ask__radio {
  flex: 0 0 auto;
  width: 14px;
  height: 14px;
  margin-top: 2px;
  border: 1.5px solid var(--faint);
  border-radius: 50%;
}
.ask__option--on .ask__radio {
  border-color: var(--ink);
  border-width: 4.5px;
}
.ask__option-body {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.ask__option-label {
  font-weight: 500;
  line-height: 1.45;
}
/* 后果说明是这一版新加的：选项不只说「是什么」，还说「选了会怎样」。 */
.ask__option-consequence {
  color: var(--muted);
  font-size: 12px;
  line-height: 1.5;
}

/* ── 自由输入 ──────────────────────────────────────────── */
.ask__note {
  width: 100%;
  padding: 8px 11px;
  color: var(--ink);
  font-size: 13px;
  background: var(--surface);
  border: 1px solid var(--line-2);
  border-radius: var(--radius-md);
  transition: border-color var(--dur-quick) var(--ease-standard);
}
.ask__note::placeholder {
  color: var(--faint);
}
.ask__note:focus {
  border-color: var(--muted);
  outline: none;
}

/* ── 动作行 ────────────────────────────────────────────── */
.ask__actions {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 10px;
}
/* 主操作，这一组里唯一的琥珀（设计规范 §1.6）。 */
.ask__primary {
  height: 34px;
  padding: 0 15px;
  color: rgb(var(--v-theme-on-primary));
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  background: var(--accent);
  border: 0;
  border-radius: var(--radius-md);
  transition: background-color 0.12s ease;
}
.ask__primary:hover:not(:disabled) {
  background: var(--accent-press);
}
.ask__primary:disabled {
  cursor: default;
  opacity: 0.45;
}
/* 「稍后处理」不是次要主操作，是一个出口：描边、不填充、不上琥珀。 */
.ask__later,
.ask__ghost {
  height: 34px;
  padding: 0 12px;
  color: var(--ink);
  font-size: 13px;
  cursor: pointer;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  transition: background-color 0.12s ease;
}
.ask__later:hover,
.ask__ghost:hover {
  background: var(--fill);
}
.ask__hint {
  margin-left: 2px;
  color: var(--faint);
  font-size: 12px;
}

/* ── 回执 ─────────────────────────────────────────────── */
.ask__receipt-main {
  display: flex;
  align-items: baseline;
  gap: 5px;
  margin: 0 0 3px;
  color: var(--ink);
  font-size: 13px;
}
.ask__ok {
  color: var(--ok);
  font-weight: 700;
}
.ask__receipt-note,
.ask__receipt-follow {
  margin: 0 0 3px;
  color: var(--muted);
  font-size: 12px;
  line-height: 1.55;
}
.ask__receipt-follow {
  margin-bottom: 9px;
}
.ask__ghost {
  height: 28px;
  padding: 0 10px;
  font-size: 12px;
}

/* ── 稍后 ─────────────────────────────────────────────── */
.ask__deferred-text {
  margin: 0 0 9px;
  color: var(--muted);
  font-size: 13px;
  line-height: 1.55;
}

/* ── 移动端 ────────────────────────────────────────────── */
/* 窄屏上动作行换行，主操作先占一整行：拇指第一下要落在「提交回答」上。 */
@media (max-width: 480px) {
  .ask {
    max-width: none;
    padding: 12px 12px 10px;
    border-radius: var(--radius-md);
  }
  .ask__actions {
    flex-wrap: wrap;
  }
  .ask__primary {
    flex: 1 1 100%;
    height: 40px;
    font-size: 14px;
  }
  .ask__later {
    flex: 1 1 100%;
    height: 40px;
    font-size: 14px;
  }
  .ask__hint {
    flex: 1 1 100%;
    margin-left: 0;
    text-align: center;
  }
}
</style>
