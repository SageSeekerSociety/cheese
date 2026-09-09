import assert from 'node:assert/strict'
import { test } from 'node:test'

import { ESLint } from 'eslint'

const eslint = new ESLint()
async function violations(template) {
  const [result] = await eslint.lintText(`<template>${template}</template>`, { filePath: 'src/AutofillFixture.vue' })
  return result.messages.filter((message) => message.ruleId === 'vue/no-restricted-syntax')
}

test('rejects unmarked native and Vuetify text fields, including a revealed password', async () => {
  for (const field of [
    '<input />',
    '<textarea />',
    '<v-text-field />',
    '<v-textarea />',
    "<v-text-field :type=\"visible ? 'text' : 'password'\" />",
  ]) {
    assert.equal((await violations(field)).length, 1, field)
  }
})

test('accepts explicit purposes and intentionally disabled autofill', async () => {
  for (const purpose of ['username', 'nickname', 'current-password', 'new-password', 'email', 'tel', 'name', 'off']) {
    assert.equal((await violations(`<v-text-field autocomplete="${purpose}" />`)).length, 0, purpose)
  }
  assert.equal((await violations('<input :autocomplete="purpose" />')).length, 0)
})

test('rejects ambiguous autocomplete values', async () => {
  for (const value of ['on', '']) {
    assert.equal((await violations(`<v-text-field autocomplete="${value}" />`)).length, 1)
  }
})

test('exempts controls without text autofill and Vuetify OTP with its built-in purpose', async () => {
  for (const type of ['file', 'hidden', 'checkbox', 'radio', 'number', 'date', 'time']) {
    assert.equal((await violations(`<input type="${type}" />`)).length, 0, type)
  }
  assert.equal((await violations('<v-otp-input />')).length, 0)
})
