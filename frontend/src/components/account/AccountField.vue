<script setup lang="ts">
// One field of an account form: its label sits above the box instead of
// floating inside it, with room at the end of the label row for a link that
// belongs to the field (「忘记密码」). The control goes in the default slot and
// must carry the id named by `inputId`.
//
// The link comes after the control in the markup and is placed beside the label
// by the grid, so Tab goes from one field's box to the next field's box and
// reaches the link after its own box rather than before it.
//
// The label's id is `<inputId>-label` on purpose: Vuetify's text fields put
// `aria-labelledby="<id>-label"` on their <input>, aiming at a label of their
// own that they do not render when the label lives out here. Taking that id
// makes the reference land on this label instead of on nothing.
defineProps<{ label: string; inputId: string }>()
</script>

<template>
  <div class="account-field">
    <label :id="`${inputId}-label`" :for="inputId" class="account-field__label">{{ label }}</label>
    <div class="account-field__control"><slot /></div>
    <div v-if="$slots.aside" class="account-field__aside"><slot name="aside" /></div>
  </div>
</template>

<style scoped>
.account-field {
  display: grid;
  grid-template-columns: 1fr auto;
  grid-template-areas:
    'label aside'
    'control control';
  align-items: baseline;
  column-gap: 12px;
  row-gap: 6px;
  margin-bottom: 16px;
}

/* A link beside the label takes the label's size. */
.account-field__label,
.account-field__aside {
  font-size: 13px;
  line-height: var(--lh-13);
}

.account-field__label {
  grid-area: label;
  font-weight: 500;
  color: var(--text);
}

.account-field__aside {
  grid-area: aside;
}

.account-field__control {
  grid-area: control;
  min-width: 0;
}
</style>
