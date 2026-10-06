<script setup lang="ts">
// The shape of "you can't open this": one icon, a heading, one line of body copy,
// and the actions that get you out. Both the project notice
// (`views/workspace/ProjectAccessNotice.vue`) and the space-management notice
// (`views/spaces/detail/ManageDeniedView.vue`) paint this, so the two read as the
// same thing instead of two looks that drift apart.
//
// Only the frame lives here — the words and the buttons are the caller's, because
// what the reader can do next differs per situation (sign in, ask for access, go
// back to the challenges).
defineOptions({ name: 'AccessNotice' })

defineProps<{
  /** mdi icon name shown above the heading. */
  icon: string
  title: string
  body: string
}>()
</script>

<template>
  <div class="access-notice">
    <v-icon size="40" class="c-faint mb-4">{{ icon }}</v-icon>
    <h1 class="t-title mb-2">{{ title }}</h1>
    <p class="t-body c-muted mb-6">{{ body }}</p>
    <div class="access-notice__actions">
      <slot name="actions" />
    </div>
    <slot name="extra" />
  </div>
</template>

<style scoped>
.access-notice {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  padding: 24px;
  text-align: center;
}

.access-notice p {
  max-width: 32em;
}

.access-notice__actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 8px;
}
</style>
