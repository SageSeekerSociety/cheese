// Shell 1: the page a document's mini-app runs in.
//
// An app is one Vue single-file component — `<script setup>` and a
// `<template>` — that 芝士 writes against the elements of components.ts. The
// document's page frames this with `sandbox="allow-scripts"` on the content
// domain (backend/app/api/app_shell_host.py) and hands the component's source
// in (runtime.ts); it is compiled here, in the frame, and mounted. The
// platform's browser, checking a new version, writes the source into the page
// instead.
//
// What an app can import is Vue and nothing else: the frame has no network,
// and every other library it may use is an element of components.ts.
import * as Vue from 'vue'
import { compileScript, compileTemplate, parse } from '@vue/compiler-sfc/dist/compiler-sfc.esm-browser.js'
import { transform } from 'sucrase'

import { requireFrom, start } from './runtime'
import { miswired } from './wiring'

const require = requireFrom({ vue: Vue })
/** The platform's elements are elements, not Vue components to resolve. */
const isCustomElement = (tag: string) => tag.startsWith('c-')

start((code, root) => {
  // The rule has to be there when the template is parsed: by compile time the
  // tags are already read as components, and `v-model` on one becomes a
  // component's `modelValue`, which the element never sees.
  const { descriptor, errors } = parse(code, { filename: 'App.vue', templateParseOptions: { isCustomElement } })
  if (errors.length) throw new Error(String(errors[0]))
  // Compiling rewrites the template's tree in place (a `v-model` is gone from
  // it afterwards), so the wiring is read from a copy taken before.
  const tree = descriptor.template?.ast && structuredClone(descriptor.template.ast)
  let js: string
  if (descriptor.scriptSetup || descriptor.script) {
    const script = compileScript(descriptor, {
      id: 'app',
      inlineTemplate: true,
      templateOptions: { compilerOptions: { isCustomElement } },
    })
    const wrong = miswired(tree, script)
    if (wrong) throw new Error(wrong)
    js = script.content
  } else {
    const wrong = miswired(tree, null)
    if (wrong) throw new Error(wrong)
    const template = compileTemplate({
      source: descriptor.template?.content ?? '',
      filename: 'App.vue',
      id: 'app',
      compilerOptions: { isCustomElement },
    })
    js = `${template.code}\nexport default { render }`
  }
  const out = transform(js, { transforms: ['typescript', 'imports'], production: true }).code
  const module = { exports: {} as Record<string, unknown> }
  new Function('require', 'module', 'exports', out)(require, module, module.exports)
  const app = Vue.createApp(module.exports.default as Vue.Component)
  app.config.compilerOptions.isCustomElement = isCustomElement
  app.config.errorHandler = (error) => {
    throw error
  }
  for (const style of descriptor.styles) {
    const el = document.createElement('style')
    el.textContent = style.content
    document.head.append(el)
  }
  app.mount(root)
})
