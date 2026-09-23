<template>
  <span>
    <!-- 新开一页：在注册表单里点开协议，填了一半的表单不能因此没了。
         @click.stop：链接长在复选框的 label 里，不拦住的话点链接会顺带勾上。 -->
    <router-link :to="{ name: 'LegalTerms' }" target="_blank" class="text-primary text-decoration-none" @click.stop>
      {{ t('account.termsOfService') }}
    </router-link>
    {{ t('account.and') }}
    <router-link :to="{ name: 'LegalPrivacy' }" target="_blank" class="text-primary text-decoration-none" @click.stop>
      {{ t('account.privacyPolicy') }}
    </router-link>
  </span>
</template>

<script setup lang="ts">
import { t } from '@/i18n'
</script>
