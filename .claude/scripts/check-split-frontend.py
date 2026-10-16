#!/usr/bin/env python3
"""A deliberately narrow, read-only frontend split check (not equivalence proof).

Usage: check-split-frontend.py --base-ref COMMIT --old-file src/Panel.vue \
           --new-file src/Panel.vue --new-file src/usePanel.ts [--repo-root DIR]
       check-split-frontend.py --self-test

The first --new-file is the replacement entry; list ALL local dependencies in
this small comparison scope, including retained files. Baseline is that exact
verified git commit, not a merge-base or a fallback. Files are working-tree
UTF-8 .vue/.ts sources. Dependencies resolve from THIS SCRIPT's frontend install,
never from the inspected repository/cwd. Business source is parsed, never run.

0: the supported contracts hold; 1: a concrete contract violation; 2: cannot
judge (takes precedence over 1). Diagnostics include a rule and source location.

Supported contracts/boundaries:
* Vue HTML templates, TS/JS script setup; an ordinary script may supply imports
  and types, not Options API state. aria-bearing elements need a unique static
  id or data-testid (tag + anchor, across the group). Expressions must retain
  their AST spelling: this is NOT a proof about renamed props or computed values.
  A .prop binding cannot replace an attribute; .attr preserves attribute mode.
  Other ARIA modifiers, duplicate/missing anchors, v-for/slot scopes, spreads/
  dynamic arguments and external/preprocessed SFC blocks are unjudged.
* TypeScript checks ONLY the named sources and installed declaration libraries.
  Actual lexical scopes, type parameters and built-in types come from its checker.
  Template expressions get lexical name probes, not Vue's full template typing.
* Props watch sources must be direct props objects/member getters (or arrays of
  these). The props object can flow unchanged through direct named function calls
  to composable parameters. Vue watch aliases/namespace imports are resolved by
  symbol, not by spelling. Callback/options AST spelling must be preserved.
  Renaming callback locals, toRef/toRefs, projected/destructured props, indirect
  calls and reactive aliases are outside this contract. Watch calls in nested
  callbacks/uncalled helpers cannot stand in for an executed props watch.
* Script imports/exports and composable return bindings are checked by TS. A
  formerly local value resolving to a library global (e.g. blur/close/focus),
  including shorthand values/templates, is a violation even when DOM accepts it.
  Existing external references are matched by named-function scope, expression
  and checker origin, never exempted by name. A collision or renamed/moved scope
  with unmatched local uses of the same name is unjudged rather than a pass.
  This does not prove arbitrary runtime control flow, event timing, side effects,
  component prop wiring or behavioral equivalence of function implementations.

AST APIs (no handwritten source parser):
https://github.com/microsoft/TypeScript/wiki/Using-the-Compiler-API
https://github.com/vuejs/core/blob/main/packages/compiler-sfc/src/parse.ts
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ENGINE = r"""
const fs = require('node:fs'), path = require('node:path');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const ts = require(path.join(input.dependencies, 'typescript'));
const vue = require(path.join(input.dependencies, '@vue/compiler-sfc'));
const findings = [];
const report = (code, rule, file, detail) => findings.push({code, rule, file, detail});
const unknown = (file, detail) => report(2, 'scope', file, detail);
const walk = (n, fn) => { fn(n); ts.forEachChild(n, c => walk(c, fn)); };
const printer = ts.createPrinter({removeComments: true});
function canonical(n, sf, replacements = new Map()) {
  const transformed = ts.transform(n, [context => root => {
    const visit = node => replacements.has(node)
      ? ts.factory.createIdentifier(replacements.get(node))
      : ts.visitEachChild(node, visit, context);
    return ts.visitNode(root, visit);
  }]);
  const result = printer.printNode(ts.EmitHint.Unspecified, transformed.transformed[0], sf);
  transformed.dispose();
  return result;
}
function expression(text, file) {
  const sf = ts.createSourceFile(file + '.expression.ts', `const __x = (${text});`,
    ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
  if (sf.parseDiagnostics.length) { unknown(file, 'template expression does not parse: ' + text); return ''; }
  return canonical(sf.statements[0].declarationList.declarations[0].initializer, sf);
}
function prepare(file, text) {
  const item = {file, text, elements: [], tags: new Set(), probes: [], setup: false};
  if (!file.endsWith('.vue')) { item.code = text + '\nexport {};'; return item; }
  const parsed = vue.parse(text, {filename: file});
  if (parsed.errors.length) { unknown(file, 'SFC parse: ' + parsed.errors.map(String).join('; ')); return item; }
  const d = parsed.descriptor;
  for (const block of [d.script, d.scriptSetup, d.template].filter(Boolean)) {
    if (block.src || (block.lang && !['ts', 'js', 'html'].includes(block.lang)))
      unknown(file, 'external/preprocessed SFC blocks are unsupported');
  }
  if (!d.scriptSetup && d.script) unknown(file, 'Options API / ordinary-script component is unsupported');
  if (d.script && d.scriptSetup) {
    const normal = ts.createSourceFile(file, d.script.content, ts.ScriptTarget.Latest, true);
    if (normal.statements.some(s => !ts.isImportDeclaration(s) && !ts.isInterfaceDeclaration(s)
      && !ts.isTypeAliasDeclaration(s) && !ts.isEmptyStatement(s)))
      unknown(file, 'ordinary script alongside setup may only contain imports/types');
  }
  function template(n) {
    if (n.type === 1) {
      item.tags.add(n.tag);
      let anchor = null;
      for (const name of ['id', 'data-testid']) {
        const p = n.props.find(p => p.type === 6 && p.name === name && p.value);
        if (p) { anchor = n.tag + '|' + name + '=' + p.value.content; break; }
      }
      const aria = {};
      for (const p of n.props) {
        if (p.type === 6 && p.name.startsWith('aria-')) aria[p.name] = 'static:' + (p.value?.content ?? '');
        if (p.type === 7) {
          if (['for', 'slot'].includes(p.name) || (['bind', 'on'].includes(p.name) && !p.arg)
            || (p.arg && !p.arg.isStatic)) unknown(file, 'template scope/spread/dynamic argument: ' + p.loc.source);
          if (p.name === 'bind' && p.arg?.isStatic && p.arg.content.startsWith('aria-')) {
            const modifiers = p.modifiers.map(m => typeof m === 'string' ? m : m.content);
            if (modifiers.some(m => !['prop', 'attr'].includes(m))
              || (modifiers.includes('prop') && modifiers.includes('attr')))
              unknown(file, 'unsupported aria binding modifiers: ' + p.loc.source);
            if (!p.exp) unknown(file, 'same-name aria binding shorthand is unsupported');
            else aria[p.arg.content] = (modifiers.includes('prop') ? 'property:' : 'bound:') + expression(p.exp.content, file);
          }
          if (p.exp && !['for', 'slot'].includes(p.name)) {
            const code = p.name === 'on' ? `(($event: any) => { ${p.exp.content}; });`
              : `void (${p.exp.content});`;
            item.probes.push(code);
          }
        }
      }
      item.elements.push({anchor, aria, line: n.loc.start.line});
    }
    if (n.type === 5) item.probes.push(`void (${n.content.content});`);
    for (const child of n.children || []) template(child);
  }
  if (d.template) template(d.template.ast);
  // Keep setup lexical scope separate from ordinary-script declarations. Move
  // only setup imports to module scope (imports are illegal inside a function).
  const setup = d.scriptSetup?.content || '';
  const parsedSetup = ts.createSourceFile(file, setup, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
  if (parsedSetup.parseDiagnostics.length) unknown(file, 'script setup does not parse');
  let imports = '', body = '';
  for (const stmt of parsedSetup.statements) {
    if (ts.isImportDeclaration(stmt)) imports += stmt.getFullText(parsedSetup) + '\n';
    else {
      if (ts.isExportDeclaration(stmt) || ts.isExportAssignment(stmt)
        || stmt.modifiers?.some(m => m.kind === ts.SyntaxKind.ExportKeyword))
        unknown(file, 'script setup exports are unsupported');
      body += stmt.getFullText(parsedSetup) + '\n';
    }
  }
  item.setup = true;
  item.code = (d.script?.content || '') + '\n' + imports + '\nfunction __split_setup__() {\n' + body;
  item.probeStart = item.code.length;
  item.code += '\n/* template probes */\n' + item.probes.join('\n') + '\n}\nexport default {};';
  return item;
}
function analyze(sources, entry, baseline) {
  const items = new Map(Object.entries(sources).map(([f, t]) => [f, prepare(f, t)]));
  if (findings.some(f => f.code === 2)) return null;
  const virtual = f => '/__split__/' + f + (f.endsWith('.vue') ? '.ts' : '');
  const byVirtual = new Map([...items.values()].map(i => [virtual(i.file), i]));
  const shim = '/__split__/macros.d.ts';
  const macros = `import * as Vue from 'vue'; declare global {
    const defineProps: typeof Vue.defineProps; const withDefaults: typeof Vue.withDefaults;
    const defineEmits: typeof Vue.defineEmits; const defineExpose: typeof Vue.defineExpose;
    const defineOptions: typeof Vue.defineOptions; const defineSlots: typeof Vue.defineSlots;
    const defineModel: typeof Vue.defineModel;
  }`;
  const options = {target: ts.ScriptTarget.ESNext, module: ts.ModuleKind.ESNext,
    moduleResolution: ts.ModuleResolutionKind.Bundler, noEmit: true, skipLibCheck: true,
    strict: false, types: [], allowNonTsExtensions: true};
  const host = ts.createCompilerHost(options);
  const originalRead = host.readFile;
  const allowed = f => f.startsWith(input.dependencies + path.sep)
    && ['.d.ts', '.d.mts', '.d.cts', '/package.json'].some(suffix => f.endsWith(suffix));
  host.readFile = f => byVirtual.get(f)?.code ?? (f === shim ? macros : allowed(f) ? originalRead(f) : undefined);
  host.fileExists = f => byVirtual.has(f) || f === shim || (allowed(f) && fs.existsSync(f));
  host.getSourceFile = (f, language) => {
    const text = host.readFile(f);
    return text === undefined ? undefined : ts.createSourceFile(f, text, language, true);
  };
  const edges = new Map([...items.keys()].map(f => [f, new Set()]));
  const localTargets = new Map();
  host.resolveModuleNames = (names, containing) => names.map(name => {
    const owner = byVirtual.get(containing);
    if (owner && name.startsWith('.')) {
      const base = path.posix.normalize(path.posix.join(path.posix.dirname(owner.file), name));
      const matches = [base, base + '.ts', base + '.vue', base + '/index.ts'].filter(f => items.has(f));
      if (matches.length !== 1) { unknown(owner.file, 'local import outside explicit scope or ambiguous: ' + name); return undefined; }
      const target = items.get(matches[0]);
      edges.get(owner.file).add(target.file);
      localTargets.set(containing + '|' + name, target);
      return {resolvedFileName: virtual(target.file), extension: ts.Extension.Ts};
    }
    const from = owner || containing === shim ? path.join(input.dependencies, '../__split__.ts') : containing;
    const resolved = ts.resolveModuleName(name, from, options, {...host,
      // Dependency directories are real; business source remains virtual only.
      directoryExists: ts.sys.directoryExists, realpath: ts.sys.realpath}).resolvedModule;
    if (!resolved || !allowed(resolved.resolvedFileName)) {
      unknown(owner?.file || containing, 'declaration dependency unavailable: ' + name); return undefined;
    }
    return resolved;
  });
  const program = ts.createProgram([...byVirtual.keys(), shim], options, host);
  const checker = program.getTypeChecker();
  for (const d of program.getGlobalDiagnostics())
    unknown(entry, 'TS environment: ' + ts.flattenDiagnosticMessageText(d.messageText, ' '));
  const symbol = n => {
    const s = ts.isShorthandPropertyAssignment(n.parent) && n.parent.name === n
      ? checker.getShorthandAssignmentValueSymbol(n.parent) : checker.getSymbolAtLocation(n);
    return s && s.flags & ts.SymbolFlags.Alias ? checker.getAliasedSymbol(s) : s;
  };
  const sourceNodes = [...byVirtual.keys()].map(f => program.getSourceFile(f));
  for (const sf of sourceNodes) {
    const item = byVirtual.get(sf.fileName);
    const scanner = ts.createScanner(ts.ScriptTarget.Latest, false, ts.LanguageVariant.Standard, sf.text);
    for (let token = scanner.scan(); token !== ts.SyntaxKind.EndOfFileToken; token = scanner.scan())
      if ([ts.SyntaxKind.SingleLineCommentTrivia, ts.SyntaxKind.MultiLineCommentTrivia].includes(token)
        && ['@ts-ignore', '@ts-nocheck', '@ts-expect-error'].some(s => scanner.getTokenText().includes(s)))
        unknown(item.file, 'TypeScript diagnostic suppression prevents checking this scope');
    for (const d of program.getSyntacticDiagnostics(sf)) unknown(item.file, 'TS parse: ' + ts.flattenDiagnosticMessageText(d.messageText, ' '));
    for (const d of program.getSemanticDiagnostics(sf)) {
      if (d.start >= item.probeStart) continue; // Template typing is not vue-tsc.
      report(baseline ? 2 : 1, 'typescript', item.file,
        `TS${d.code} line ${sf.getLineAndCharacterOfPosition(d.start || 0).line + 1}: `
        + ts.flattenDiagnosticMessageText(d.messageText, ' '));
    }
  }
  const reached = new Set();
  function reach(f) { if (reached.has(f)) return; reached.add(f); for (const e of edges.get(f) || []) reach(e); }
  reach(entry);
  if (!baseline) for (const f of items.keys()) if (!reached.has(f)) unknown(f, 'specified file is not imported from the replacement entry');
  // SFC extraction needs an actual component use, not an unrelated import whose
  // aria attributes could compensate for a loss in the entry.
  const rendered = new Set([entry]);
  if (!baseline) for (const sf of sourceNodes) {
    const item = byVirtual.get(sf.fileName);
    for (const s of sf.statements.filter(ts.isImportDeclaration)) {
      const imported = s.importClause?.name;
      const child = localTargets.get(sf.fileName + '|' + s.moduleSpecifier.text);
      if (child?.file.endsWith('.vue')) {
        if (!imported || s.importClause.isTypeOnly) { unknown(child.file, 'SFC must be a rendered default value import'); continue; }
        const kebab = imported.text.replace(/[A-Z]/g, c => '-' + c.toLowerCase()).replace(/^-/, '');
        if (item.tags.has(imported.text) || item.tags.has(kebab)) rendered.add(child.file);
      }
    }
  }
  if (!baseline) for (const f of items.keys())
    if (f.endsWith('.vue') && !rendered.has(f)) unknown(f, 'imported SFC is not rendered by its consumer');
  const names = new Set(), props = new Set(), active = new Set(), calls = [], references = [];
  const bindNames = n => {
    if (ts.isIdentifier(n)) names.add(n.text);
    else if (ts.isObjectBindingPattern(n) || ts.isArrayBindingPattern(n))
      for (const element of n.elements) if (ts.isBindingElement(element)) bindNames(element.name);
  };
  const ownerFunction = n => { for (let p = n.parent; p; p = p.parent) if (ts.isFunctionLike(p)) return p; return null; };
  const isVue = (n, name) => {
    const s = symbol(n);
    return s?.name === name && s.declarations?.some(d => d.getSourceFile().fileName.startsWith(input.dependencies + '/')
      && d.getSourceFile().fileName.includes('/@vue/'));
  };
  const unwrap = n => {
    while (n && (ts.isParenthesizedExpression(n) || ts.isAsExpression(n) || ts.isNonNullExpression(n))) n = n.expression;
    return n;
  };
  const isProps = n => n && ts.isIdentifier(unwrap(n)) && props.has(symbol(unwrap(n)));
  const isMacro = (n, name) => ts.isIdentifier(n) && n.text === name
    && (isVue(n, name) || symbol(n)?.declarations?.some(d => d.getSourceFile().fileName === shim));
  for (const sf of sourceNodes) walk(sf, n => {
    if (ts.isFunctionDeclaration(n) && n.name?.text === '__split_setup__') active.add(n);
    if ((ts.isVariableDeclaration(n) || ts.isFunctionDeclaration(n) || ts.isClassDeclaration(n) || ts.isParameter(n)) && n.name) bindNames(n.name);
    if (ts.isCallExpression(n)) calls.push(n);
    if (ts.isVariableDeclaration(n) && n.initializer) {
      let init = unwrap(n.initializer);
      if (ts.isCallExpression(init) && isMacro(init.expression, 'withDefaults')) init = init.arguments[0];
      if (init && ts.isCallExpression(init) && isMacro(init.expression, 'defineProps')) {
        if (!ts.isIdentifier(n.name)) unknown(byVirtual.get(sf.fileName).file, 'destructured defineProps is unsupported');
        else if (byVirtual.get(sf.fileName).file === entry) props.add(symbol(n.name));
      }
    }
  });
  // Fixed point over direct calls only: no execution, no invocation of imports.
  let changed = true;
  while (changed) {
    changed = false;
    for (const call of calls) {
      if (ownerFunction(call) && !active.has(ownerFunction(call))) continue;
      const s = symbol(call.expression);
      for (let fn of s?.declarations || []) {
        if (ts.isVariableDeclaration(fn)) fn = fn.initializer;
        if (!fn || !ts.isFunctionLike(fn) || !byVirtual.has(fn.getSourceFile().fileName)) continue;
        if (!active.has(fn)) { active.add(fn); changed = true; }
        fn.parameters.forEach((p, i) => {
          if (!isProps(call.arguments[i])) return;
          if (!ts.isIdentifier(p.name)) unknown(byVirtual.get(fn.getSourceFile().fileName).file, 'destructured composable props parameter');
          else if (!props.has(symbol(p.name))) { props.add(symbol(p.name)); changed = true; }
        });
      }
    }
  }
  // Refuse indirect reactive sources rather than silently losing the baseline
  // facts. This is dependency discovery, not evaluation of the initializer.
  const derived = new Set();
  changed = true;
  while (changed) {
    changed = false;
    for (const sf of sourceNodes) walk(sf, n => {
      if (!(ts.isVariableDeclaration(n) || ts.isFunctionDeclaration(n)) || !n.name || !ts.isIdentifier(n.name)) return;
      const own = symbol(n.name), value = n.initializer || n.body;
      if (!value || props.has(own) || derived.has(own)) return;
      let dependent = false;
      walk(value, child => { if (ts.isIdentifier(child) && (props.has(symbol(child)) || derived.has(symbol(child)))) dependent = true; });
      if (dependent) { derived.add(own); changed = true; }
    });
  }
  const conditional = n => {
    for (let p = n.parent; p && !ts.isFunctionLike(p); p = p.parent)
      if (ts.isIfStatement(p) || ts.isIterationStatement(p, false) || ts.isConditionalExpression(p)
        || ts.isSwitchStatement(p) || ts.isTryStatement(p) || ts.isBinaryExpression(p)) return true;
    return false;
  };
  for (const call of calls) if (call.arguments.some(isProps) && conditional(call))
    unknown(byVirtual.get(call.getSourceFile().fileName).file, 'conditional props call is outside the direct-call contract');
  const watches = [];
  for (const call of calls) {
    if (!isVue(call.expression, 'watch')) continue;
    const sf = call.getSourceFile(), file = byVirtual.get(sf.fileName).file;
    const replacements = new Map();
    walk(call, n => { if (ts.isIdentifier(n) && props.has(symbol(n))) replacements.set(n, '__props'); });
    let sourceProps = false;
    if (call.arguments[0]) walk(call.arguments[0], n => { if (replacements.has(n)) sourceProps = true; });
    if (!sourceProps) {
      let indirect = false;
      if (call.arguments[0]) walk(call.arguments[0], n => {
        if (!ts.isIdentifier(n)) return;
        const s = symbol(n);
        if (derived.has(s) || s?.declarations?.some(ts.isParameter)) indirect = true;
      });
      if (indirect) unknown(file, 'watch source has indirect/unbound props provenance');
      continue;
    }
    if (conditional(call)) unknown(file, 'conditional props watch is unsupported');
    const simpleSource = n => {
      n = unwrap(n);
      if (!n) return false;
      if (isProps(n)) return true;
      if (ts.isPropertyAccessExpression(n)) return simpleSource(n.expression);
      if (ts.isElementAccessExpression(n)) return ts.isStringLiteral(n.argumentExpression) && simpleSource(n.expression);
      if (ts.isArrowFunction(n)) return !ts.isBlock(n.body) && simpleSource(n.body);
      if (ts.isArrayLiteralExpression(n)) return n.elements.every(simpleSource);
      return false;
    };
    if (!simpleSource(call.arguments[0])) { unknown(file, 'unsupported props watch source: ' + call.arguments[0].getText(sf)); continue; }
    if (ownerFunction(call) && !active.has(ownerFunction(call))) {
      if (baseline) unknown(file, 'baseline props watch is in a callback/indirect or uncalled helper');
      continue;
    }
    watches.push(call.arguments.map(a => canonical(a, sf, replacements)).join('|'));
  }
  // A declaration's spelling is only a candidate filter. Preserve the origin
  // of each VALUE reference, not a name-wide exemption for existing globals.
  const referenceKey = (n, sf) => {
    const scopes = [];
    for (let p = n.parent; p; p = p.parent) if (ts.isFunctionLike(p)) {
      const name = p.name || (ts.isVariableDeclaration(p.parent) ? p.parent.name : null);
      if (name && ts.isIdentifier(name) && name.text === '__split_setup__') continue;
      scopes.unshift(name ? canonical(name, sf) : `anonymous:${p.kind}:${p.parameters.map(a => canonical(a.name, sf)).join(',')}`);
    }
    let use = n;
    while (use.parent && [ts.isCallExpression, ts.isNewExpression, ts.isPropertyAccessExpression,
      ts.isElementAccessExpression, ts.isParenthesizedExpression, ts.isNonNullExpression,
      ts.isPropertyAssignment, ts.isShorthandPropertyAssignment].some(test => test(use.parent))) use = use.parent;
    return JSON.stringify([n.text, scopes, canonical(use, sf)]);
  };
  // Indirect props routing cannot be accepted as an empty watch fact set.
  for (const sf of sourceNodes) walk(sf, n => {
    const item = byVirtual.get(sf.fileName);
    if (ts.isVariableDeclaration(n) && n.initializer && isProps(n.initializer)) unknown(item.file, 'props aliases/destructuring are unsupported');
    if (!ts.isIdentifier(n)) return;
    const isName = (ts.isPropertyAccessExpression(n.parent) && n.parent.name === n)
      || ((ts.isPropertyAssignment(n.parent) || ts.isMethodDeclaration(n.parent)) && n.parent.name === n)
      || (ts.isBindingElement(n.parent) && n.parent.propertyName === n);
    if (isName) return;
    const s = symbol(n);
    if (item.probeStart !== undefined && n.pos >= item.probeStart && !s
      && !ts.isTypeNode(n.parent) && n.text !== 'undefined')
      report(baseline ? 2 : 1, 'binding', item.file, 'unbound template identifier: ' + n.text);
    const raw = checker.getSymbolAtLocation(n);
    if (!ts.isShorthandPropertyAssignment(n.parent) && raw?.declarations?.some(d => d.name === n)) return;
    // ExpressionWithTypeArguments is a TypeNode by kind, but its expression
    // in class extends is runtime. TS's contextual API keeps that expression
    // while excluding type arguments, interface heritage and implements.
    for (let p = n.parent; p; p = p.parent)
      if (ts.isPartOfTypeNode(p) || ts.isImportDeclaration(p) || ts.isExportDeclaration(p)) return;
    if (!s?.declarations?.length) return; // Unresolved values are diagnosed above/by TS.
    const local = s.declarations.some(d => byVirtual.has(d.getSourceFile().fileName));
    if (local) names.add(n.text);
    const origin = local ? 'local' : JSON.stringify(s.declarations.map(d =>
      [d.getSourceFile().fileName, d.pos, s.name]).sort());
    references.push({name: n.text, key: referenceKey(n, sf), origin, file: item.file});
  });
  return {items, watches, names, references};
}
function checkBindings(old, current) {
  for (const name of old.names) {
    const before = old.references.filter(r => r.name === name);
    const after = current.references.filter(r => r.name === name);
    const external = after.filter(r => r.origin !== 'local');
    if (!external.length) continue;
    let violation = null, ambiguous = null;
    const used = new Map();
    for (const ref of external) {
      const matches = before.filter(r => r.key === ref.key);
      const origins = new Set(matches.map(r => r.origin));
      if ((matches.length && origins.size === 1 && origins.has('local'))
        || !before.some(r => r.origin !== 'local')) violation = ref;
      else if (origins.size === 1 && origins.has(ref.origin)) {
        const count = (used.get(ref.key) || 0) + 1;
        used.set(ref.key, count);
        if (count > matches.length) ambiguous = ref;
      } else ambiguous = ref;
    }
    // Even unchanged global uses cannot excuse a lost local use in another
    // scope. Renamed/moved scopes and collisions require a human, not a name
    // whitelist or a total-count comparison that could cancel two changes.
    for (const ref of before.filter(r => r.origin === 'local')) {
      const needed = before.filter(r => r.key === ref.key && r.origin === 'local').length;
      const retained = after.filter(r => r.key === ref.key && r.origin === 'local').length;
      if (retained < needed) ambiguous = external[0];
    }
    if (violation) report(1, 'binding', violation.file,
      'formerly local ' + name + ' now resolves outside the split (possibly a DOM global)');
    else if (ambiguous) unknown(ambiguous.file, 'ambiguous lexical provenance for ' + name
      + ': cannot match external references and retained local uses by scope/expression');
  }
}
function anchors(result, baseline) {
  const map = new Map();
  for (const item of result.items.values()) {
    if (baseline && item.file !== input.oldFile) continue;
    for (const e of item.elements) {
      if (!e.anchor) {
        if (Object.keys(e.aria).length) unknown(item.file, `line ${e.line}: aria element needs static id/data-testid`);
        continue;
      }
      if (map.has(e.anchor)) unknown(item.file, 'ambiguous repeated element anchor: ' + e.anchor);
      map.set(e.anchor, {...e, file: item.file});
    }
  }
  return map;
}
const old = analyze(input.old, input.oldFile, true);
const current = old && analyze(input.current, input.entry, false);
if (old && current) {
  checkBindings(old, current);
  const before = anchors(old, true), after = anchors(current, false);
  for (const [key, element] of before) {
    if (!Object.keys(element.aria).length) continue;
    const target = after.get(key);
    if (!target) { unknown(element.file, 'original aria element cannot be matched: ' + key); continue; }
    for (const [attr, value] of Object.entries(element.aria)) if (target.aria[attr] !== value)
      report(1, 'aria', target.file, `${key}: ${attr} lost or changed (${value} -> ${target.aria[attr] ?? 'missing'})`);
  }
  const remaining = [...current.watches];
  for (const watch of old.watches) {
    const i = remaining.indexOf(watch);
    if (i < 0) report(1, 'watch', input.entry, 'props watch lost or changed: ' + watch);
    else remaining.splice(i, 1);
  }
}
console.log(JSON.stringify({findings, facts: old && current ? {
  baselineFiles: old.items.size, currentFiles: current.items.size,
  baselinePropsWatches: old.watches.length, currentPropsWatches: current.watches.length,
  baselineAriaElements: [...old.items.values()].filter(i => i.file === input.oldFile)
    .flatMap(i => i.elements).filter(e => Object.keys(e.aria).length).length
} : null}));
"""


def git(root: Path, *args: str, optional: bool = False) -> str | None:
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=30)
    if result.returncode:
        if optional:
            return None
        raise ValueError(f"git {' '.join(args)}: {result.stderr.strip()}")
    return result.stdout


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-ref")
    parser.add_argument("--old-file")
    parser.add_argument("--new-file", action="append")
    parser.add_argument("--repo-root", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    script = Path(__file__).resolve()
    if args.self_test:
        if any((args.base_ref, args.old_file, args.new_file, args.repo_root)):
            parser.error("--self-test cannot be mixed with comparison arguments")
        return subprocess.run([sys.executable, str(script.with_name("test-check-split-frontend.py"))], check=False).returncode
    if not args.base_ref or not args.old_file or not args.new_file:
        parser.error("--base-ref, --old-file and at least one --new-file are required")
    try:
        root = (args.repo_root or Path.cwd()).resolve(strict=True)
        top = Path(git(root, "rev-parse", "--show-toplevel").strip()).resolve()
        if root != top:
            raise ValueError("--repo-root must be the git working-tree root")
        commit = git(root, "rev-parse", "--verify", "--end-of-options", args.base_ref + "^{commit}").strip()
        files = [args.old_file, *args.new_file]
        for file in files:
            p = Path(file)
            if p.is_absolute() or ".." in p.parts or p.as_posix() != file or p.suffix not in (".ts", ".vue"):
                raise ValueError(f"expected normalized repository-relative .ts/.vue path: {file}")
        if len(set(args.new_file)) != len(args.new_file):
            raise ValueError("duplicate --new-file")
        old = {args.old_file: git(root, "show", f"{commit}:{args.old_file}")}
        current = {}
        for file in args.new_file:
            target = root / file
            if not target.resolve(strict=True).is_relative_to(root):
                raise ValueError(f"file escapes repository: {file}")
            current[file] = target.read_text(encoding="utf-8")
            if file not in old:
                text = git(root, "show", f"{commit}:{file}", optional=True)
                if text is not None:
                    old[file] = text
        dependencies = (script.parents[2] / "frontend/node_modules").resolve()
        for package in ("typescript", "@vue/compiler-sfc"):
            if not (dependencies / package / "package.json").is_file():
                raise ValueError(f"missing parser dependency in script repository: {package} ({dependencies})")
        node = shutil.which("node")
        if not node:
            raise ValueError("node is unavailable")
        payload = {"dependencies": str(dependencies), "old": old, "current": current,
                   "oldFile": args.old_file, "entry": args.new_file[0]}
        env = {k: v for k, v in os.environ.items() if k not in ("NODE_OPTIONS", "NODE_PATH")}
        result = subprocess.run([node, "-e", ENGINE], input=json.dumps(payload), text=True,
                                capture_output=True, cwd=script.parents[2], env=env, timeout=90)
        if result.returncode:
            raise ValueError("AST engine failed: " + result.stderr.strip())
        answer = json.loads(result.stdout)
        code = max((f["code"] for f in answer["findings"]), default=0)
        for finding in answer["findings"]:
            print(f"[{finding['code']} {finding['rule']}] {finding['file']}: {finding['detail']}")
        print(f"{'PASS (bounded contracts only)' if code == 0 else 'VIOLATION' if code == 1 else 'CANNOT JUDGE'} "
              f"base={commit} facts={json.dumps(answer['facts'], sort_keys=True)}")
        return code
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"CANNOT JUDGE: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
