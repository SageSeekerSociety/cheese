<script setup lang="ts">
// One field of an account form: its label sits above the box instead of
// floating inside it, with room at the end of the label row for a link that
// belongs to the field (「忘记密码」). The control goes in the default slot and
// must carry the id named by `inputId`.
defineProps<{ label: string; inputId: string }>()
</script>

<template>
  <div class="account-field">
    <div class="account-field__head">
      <label :for="inputId" class="account-field__label">{{ label }}</label>
      <slot name="aside" />
    </div>
    <slot />
  </div>
</template>

<style scoped>
.account-field {
  display: grid;
  gap: 6px;
  margin-bottom: 16px;
}

/* A link beside the label takes the label's size. */
.account-field__head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  font-size: 13px;
  line-height: var(--lh-13);
}

.account-field__label {
  font-weight: 500;
  color: var(--text);
}
</style>
