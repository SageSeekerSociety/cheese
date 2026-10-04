<script setup lang="ts">
/**
 * 知是的一个表单栏：把**一个控件**（默认插槽）和它的标签围在一起。
 *
 * 标签在框外（13px / 500 / --text，和 `components/account/AccountField` 一致）：
 * outlined 的浮动标签骑在边框上，半个字高在框外，两栏叠起来时下面那一栏的标签会
 * 压在上面那一栏的边框上（`.claude/rules/frontend.md`）。标签在框外就没有这件事。
 *
 * 它只做控件做不了的那几件：
 *
 * - **id 与标签关联**：不给 `id` 就生成一个，`<label for>` 认它；控件的 id 从插槽
 *   属性拿（`v-slot="{ id }"`），原生 `<input>` 和 `v-text-field` 都收得住。
 * - **aria**：`aria-describedby`（提示 / 报错 / 字数三者的 id）、`aria-invalid`
 *   （有报错时 true）、`aria-required` 都从插槽属性给，调用处 `v-bind` 到控件上。
 *   Vuetify 自己会挂 `aria-describedby`，但**不会**挂 `aria-invalid`；这里补上。
 * - **必填标记**：一个 `aria-hidden` 的 `*` 加一句读屏才念的「必填」——只看得到 `*`，
 *   读屏听得到字，两边都不落空。
 * - **选填下标**：`optional` 时标签后面跟一段「（选填）」。
 * - **报错**：`error` 那一行，`--danger-ink` 写字（`--danger` 只配当标记色）。
 * - **字数**：`counter` 给 `{ current, max }`，画成「12/200」。
 *
 * 用法：
 *
 * ```vue
 * <BaseField v-model:... :label="t('account.profile.nickname')" required :counter="{ current: n, max: 20 }">
 *   <template #default="{ id, describedby, invalid, required }">
 *     <input :id="id" :aria-describedby="describedby" :aria-invalid="invalid" :aria-required="required" />
 *   </template>
 * </BaseField>
 * ```
 *
 * `label` 不给时只围住控件：给已经自带标签的控件（例如平台自绘的原生输入框）补提示、
 * 报错和字数。
 */
import { computed, useId } from 'vue'
import { useI18n } from 'vue-i18n'

const props = withDefaults(
  defineProps<{
    /** 标签文字。不给就只围住控件。 */
    label?: string
    /** 控件下面那行提示。 */
    hint?: string
    /** 必填：标签后跟一个 `*`（读屏另念一句「必填」）。 */
    required?: boolean
    /** 选填：标签后跟「（选填）」。和 `required` 同时给时 `required` 优先。 */
    optional?: boolean
    /** 报错那一行；有它时 `invalid` 为 true。 */
    error?: string
    /** 字数，画成「current/max」。 */
    counter?: { current: number; max: number }
    /** 控件的 id；不给就生成一个。 */
    id?: string
  }>(),
  {
    label: undefined,
    hint: undefined,
    required: false,
    optional: false,
    error: undefined,
    counter: undefined,
    id: undefined,
  }
)

const { t } = useI18n()

// useId 只能在 setup 里调一次；生成的 id 每个实例不同，标签的 for 认它。
const uid = useId()
const controlId = computed(() => props.id ?? `base-field-${uid}`)
const hintId = computed(() => (props.hint ? `${controlId.value}-hint` : undefined))
const errorId = computed(() => (props.error ? `${controlId.value}-error` : undefined))
const counterId = computed(() => (props.counter ? `${controlId.value}-counter` : undefined))

/** 三个描述行里出现的那些 id，按顺序拼给 `aria-describedby`。 */
const describedby = computed(
  () => [hintId.value, errorId.value, counterId.value].filter((id): id is string => Boolean(id)).join(' ') || undefined
)

const invalid = computed(() => Boolean(props.error))
const counterText = computed(() => (props.counter ? `${props.counter.current}/${props.counter.max}` : ''))

const fieldSlotProps = computed(() => ({
  id: controlId.value,
  describedby: describedby.value,
  invalid: invalid.value,
  required: props.required,
}))
</script>

<template>
  <div class="base-field">
    <div v-if="label" class="base-field__head">
      <label class="base-field__label" :for="controlId">
        {{ label }}
        <template v-if="required">
          <!-- The star is decoration; the words after it are what a screen reader reads. -->
          <span class="base-field__req" aria-hidden="true">*</span>
          <span class="visually-hidden">{{ t('global.required') }}</span>
        </template>
        <span v-else-if="optional" class="base-field__optional">{{ t('global.optional') }}</span>
      </label>
    </div>
    <div class="base-field__control">
      <slot v-bind="fieldSlotProps" />
    </div>
    <p v-if="hint" :id="hintId" class="base-field__hint">{{ hint }}</p>
    <p v-if="error" :id="errorId" class="base-field__error">{{ error }}</p>
    <p v-if="counter" :id="counterId" class="base-field__counter t-num">{{ counterText }}</p>
  </div>
</template>

<style scoped>
.base-field {
  display: grid;
  gap: 6px;
}

.base-field__head {
  display: flex;
  gap: 8px;
  align-items: baseline;
}

.base-field__label {
  font-size: 13px;
  font-weight: 500;
  line-height: var(--lh-13);
  color: var(--text);
}

/* 只给看得见的人看：`--danger` 是标记色，写这个字对比度不够（docs/design-system.md §1.5）。 */
.base-field__req {
  color: var(--danger-ink);
}

.base-field__optional {
  color: var(--muted);
}

.base-field__hint {
  margin: 0;
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--muted);
}

.base-field__error {
  margin: 0;
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--danger-ink);
}

.base-field__counter {
  margin: 0;
  font-size: 12px;
  line-height: var(--lh-12);
  color: var(--muted);
  text-align: end;
}
</style>
