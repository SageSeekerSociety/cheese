// The Monaco side of a task's language servers: each file of the checkout open in
// an editor is a model of its own, kept equal on the server, and Monaco's own
// widgets — squiggles, the completion list, hover, peek and go to — answer from
// the server through the providers registered here.
//
// Loaded only by an editor that was given a `LanguageLink`, after Monaco itself.
// Monaco is one registry for the whole page, so the providers and the editor
// opener are registered once; each looks up whose model it was asked about and
// answers nothing for a model that is not a task file with a server up.
//
// A model is named `cheese-task://<task>/<path>`, not by the server's
// `file://<root>/<path>`: two tasks on one page can have their checkouts at the
// same path on their machines, and the root is not known until a server is up.
// The server's URIs are translated at this boundary, both ways.
import type * as Monaco from 'monaco-editor'
import type {
  CompletionItem,
  Diagnostic,
  Hover,
  Location,
  LocationLink,
  MarkedString,
  MarkupContent,
  Position,
  Range,
} from 'vscode-languageserver-protocol'
import type { LspClient } from './client'
import type { LanguageLink } from './link'

import { checkoutPath } from './uri'

type MonacoApi = typeof Monaco
type Model = Monaco.editor.ITextModel

export const SCHEME = 'cheese-task'
/** Whose markers these are, for `setModelMarkers`. */
const OWNER = 'language-server'

/** The model URI of a file of a task's checkout. */
export function modelUri(monaco: MonacoApi, scope: string, path: string): Monaco.Uri {
  return monaco.Uri.from({ scheme: SCHEME, authority: scope, path: `/${path.replace(/^\/+/, '')}` })
}

/** Which task and file a model URI names; null for any other URI. */
function placeOf(uri: Monaco.Uri): { scope: string; path: string } | null {
  if (uri.scheme !== SCHEME || !uri.path.startsWith('/')) return null
  return { scope: uri.authority, path: uri.path.slice(1) }
}

const sameScope = (a: string, b: string) => a.toLowerCase() === b.toLowerCase()

// ---------------------------------------------------------------- positions

/** LSP lines and characters count from 0, Monaco's from 1; both count UTF-16 code units. */
export function toPosition(position: Monaco.IPosition): Position {
  return { line: position.lineNumber - 1, character: position.column - 1 }
}

export function toRange(range: Range): Monaco.IRange {
  return {
    startLineNumber: range.start.line + 1,
    startColumn: range.start.character + 1,
    endLineNumber: range.end.line + 1,
    endColumn: range.end.character + 1,
  }
}

// ---------------------------------------------------------------- documents

interface Document {
  model: Model
  scope: string
  path: string
  refs: number
  /** The server the model is open on now. */
  client: LspClient | null
  serverUri: string | null
  stop: () => void
}

const documents = new Map<string, Document>()

/**
 * The model of a file of the link's checkout, made on first use and shared by every editor
 * showing that file. While it is open, a server for its language (if any) has its text.
 * Each call is matched by one `closeDocument`.
 */
export function openDocument(
  monaco: MonacoApi,
  link: LanguageLink,
  path: string,
  languageId: string,
  text: string
): Model {
  const uri = modelUri(monaco, link.scope, path)
  const key = uri.toString()
  const known = documents.get(key)
  if (known) {
    known.refs += 1
    return known.model
  }
  const model = monaco.editor.getModel(uri) ?? monaco.editor.createModel(text, languageId, uri)
  const doc: Document = { model, scope: link.scope, path, refs: 1, client: null, serverUri: null, stop: () => {} }
  documents.set(key, doc)

  const server = link.serverFor(languageId)
  if (server) {
    ensureFeatures(monaco, languageId)
    let stopDiagnostics = () => {}
    const detach = () => {
      stopDiagnostics()
      stopDiagnostics = () => {}
      if (doc.client && doc.serverUri) doc.client.close(doc.serverUri)
      doc.client = null
      doc.serverUri = null
      if (!model.isDisposed()) monaco.editor.setModelMarkers(model, OWNER, [])
    }
    const stopUsing = server.use((client) => {
      detach()
      if (!client || client.isClosed) return
      const serverUri = client.uriOf(path)
      doc.client = client
      doc.serverUri = serverUri
      stopDiagnostics = client.onDiagnostics((published) => {
        if (checkoutPath(client.root, published.uri) !== path || model.isDisposed()) return
        monaco.editor.setModelMarkers(
          model,
          OWNER,
          published.diagnostics.map((d) => toMarker(monaco, d))
        )
      })
      client.open(serverUri, languageId, model.getValue())
    })
    const changes = model.onDidChangeContent(() => {
      if (doc.client && doc.serverUri) doc.client.change(doc.serverUri, model.getValue())
    })
    doc.stop = () => {
      changes.dispose()
      stopUsing()
      detach()
    }
  }
  return model
}

/** An editor is done with a model `openDocument` gave it; the last one disposes it. */
export function closeDocument(model: Model): void {
  const key = model.uri.toString()
  const doc = documents.get(key)
  if (!doc || doc.model !== model) return
  doc.refs -= 1
  if (doc.refs > 0) return
  documents.delete(key)
  doc.stop()
  model.dispose()
}

/** The server a model is open on, or null when it is not a task file with a server up. */
function served(model: Model): { client: LspClient; uri: string; doc: Document } | null {
  const doc = documents.get(model.uri.toString())
  if (!doc || doc.model !== model || !doc.client || !doc.serverUri || doc.client.isClosed) return null
  return { client: doc.client, uri: doc.serverUri, doc }
}

/** A server URI as Monaco names it: a file of the checkout is that file's model URI. */
function locate(monaco: MonacoApi, doc: Document, client: LspClient, uri: string): Monaco.Uri {
  const path = checkoutPath(client.root, uri)
  return path === null ? monaco.Uri.parse(uri) : modelUri(monaco, doc.scope, path)
}

// ---------------------------------------------------------------- conversions

export function toMarker(monaco: MonacoApi, d: Diagnostic): Monaco.editor.IMarkerData {
  const severity =
    d.severity === 2
      ? monaco.MarkerSeverity.Warning
      : d.severity === 3
        ? monaco.MarkerSeverity.Info
        : d.severity === 4
          ? monaco.MarkerSeverity.Hint
          : monaco.MarkerSeverity.Error
  const code = d.code === undefined ? undefined : String(d.code)
  return {
    ...toRange(d.range),
    severity,
    // LSP 3.18 lets a message be Markdown; a marker shows text, so its source text it is.
    message: typeof d.message === 'string' ? d.message : d.message.value,
    source: d.source,
    code: code && d.codeDescription ? { value: code, target: monaco.Uri.parse(d.codeDescription.href) } : code,
    tags: d.tags?.map((tag) => (tag === 1 ? monaco.MarkerTag.Unnecessary : monaco.MarkerTag.Deprecated)),
  }
}

/** LSP `CompletionItemKind` (1-based) by name; Monaco's enum has every one of these names. */
const KINDS = [
  'Text',
  'Method',
  'Function',
  'Constructor',
  'Field',
  'Variable',
  'Class',
  'Interface',
  'Module',
  'Property',
  'Unit',
  'Value',
  'Enum',
  'Keyword',
  'Snippet',
  'Color',
  'File',
  'Reference',
  'Folder',
  'EnumMember',
  'Constant',
  'Struct',
  'Event',
  'Operator',
  'TypeParameter',
] as const

function kindOf(monaco: MonacoApi, kind: number | undefined): Monaco.languages.CompletionItemKind {
  const name = kind ? KINDS[kind - 1] : undefined
  return monaco.languages.CompletionItemKind[name ?? 'Text']
}

/** Plain text shown as Markdown: every character Markdown would read is escaped. */
function plainMarkdown(text: string): string {
  return text.replace(/[\\`*_{}[\]()#+\-.!|<>~]/g, '\\$&').replace(/\n/g, '  \n')
}

function documentation(doc: string | MarkupContent | undefined): string | Monaco.IMarkdownString | undefined {
  if (doc === undefined || typeof doc === 'string') return doc
  return doc.kind === 'markdown' ? { value: doc.value } : doc.value
}

export function hoverContents(contents: Hover['contents']): Monaco.IMarkdownString[] {
  const one = (c: MarkedString | MarkupContent): Monaco.IMarkdownString | null => {
    if (typeof c === 'string') return c ? { value: c } : null
    if ('kind' in c) return c.value ? { value: c.kind === 'markdown' ? c.value : plainMarkdown(c.value) } : null
    return { value: `\`\`\`${c.language}\n${c.value}\n\`\`\`` }
  }
  const list = Array.isArray(contents) ? contents : [contents]
  return list.map(one).filter((c): c is Monaco.IMarkdownString => c !== null)
}

/** The LSP item a Monaco suggestion came from, for `completionItem/resolve`. */
const origins = new WeakMap<Monaco.languages.CompletionItem, { item: CompletionItem; client: LspClient }>()

function toSuggestion(
  monaco: MonacoApi,
  item: CompletionItem,
  fallback: Monaco.IRange,
  client: LspClient
): Monaco.languages.CompletionItem {
  const edit = item.textEdit
  let range: Monaco.languages.CompletionItem['range'] = fallback
  let insertText = item.insertText ?? item.label
  if (edit) {
    insertText = edit.newText
    range = 'range' in edit ? toRange(edit.range) : { insert: toRange(edit.insert), replace: toRange(edit.replace) }
  }
  const suggestion: Monaco.languages.CompletionItem = {
    label: item.labelDetails
      ? { label: item.label, detail: item.labelDetails.detail, description: item.labelDetails.description }
      : item.label,
    kind: kindOf(monaco, item.kind),
    detail: item.detail,
    documentation: documentation(item.documentation),
    sortText: item.sortText,
    filterText: item.filterText,
    preselect: item.preselect,
    insertText,
    insertTextRules:
      item.insertTextFormat === 2 ? monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet : undefined,
    range,
    commitCharacters: item.commitCharacters,
    additionalTextEdits: item.additionalTextEdits?.map((e) => ({ range: toRange(e.range), text: e.newText })),
    tags: item.deprecated || item.tags?.includes(1) ? [monaco.languages.CompletionItemTag.Deprecated] : undefined,
  }
  origins.set(suggestion, { item, client })
  return suggestion
}

function toLocations(
  monaco: MonacoApi,
  at: { client: LspClient; doc: Document },
  result: Location | Location[] | LocationLink[] | null
): Monaco.languages.LocationLink[] {
  if (!result) return []
  const list: (Location | LocationLink)[] = Array.isArray(result) ? result : [result]
  return list.map((l) =>
    'targetUri' in l
      ? {
          uri: locate(monaco, at.doc, at.client, l.targetUri),
          range: toRange(l.targetRange),
          targetSelectionRange: toRange(l.targetSelectionRange),
          originSelectionRange: l.originSelectionRange && toRange(l.originSelectionRange),
        }
      : { uri: locate(monaco, at.doc, at.client, l.uri), range: toRange(l.range) }
  )
}

// ---------------------------------------------------------------- providers

/** C and C++ servers ask for completion after these; a server's own list is checked per request. */
const TRIGGER_CHARACTERS = ['.', '<', '>', ':', '"', '/', '*']

/** What Monaco asks for a task file, answered from its server; exported for the tests. */
export function providers(monaco: MonacoApi) {
  const completion: Monaco.languages.CompletionItemProvider = {
    triggerCharacters: TRIGGER_CHARACTERS,
    async provideCompletionItems(model, position, context, token) {
      const at = served(model)
      if (!at) return undefined
      const provider = at.client.capabilities.completionProvider
      if (!provider) return undefined
      const Kind = monaco.languages.CompletionTriggerKind
      if (context.triggerKind === Kind.TriggerCharacter) {
        if (!context.triggerCharacter || !(provider.triggerCharacters ?? []).includes(context.triggerCharacter))
          return undefined
      }
      let result
      try {
        result = await at.client.completion(
          at.uri,
          toPosition(position),
          context.triggerKind === Kind.TriggerCharacter
            ? { triggerKind: 2, triggerCharacter: context.triggerCharacter }
            : { triggerKind: context.triggerKind === Kind.TriggerForIncompleteCompletions ? 3 : 1 },
          token
        )
      } catch {
        return undefined
      }
      if (!result) return undefined
      const word = model.getWordUntilPosition(position)
      const fallback = {
        startLineNumber: position.lineNumber,
        startColumn: word.startColumn,
        endLineNumber: position.lineNumber,
        endColumn: word.endColumn,
      }
      const items = Array.isArray(result) ? result : result.items
      return {
        suggestions: items.map((item) => toSuggestion(monaco, item, fallback, at.client)),
        incomplete: !Array.isArray(result) && result.isIncomplete,
      }
    },
    async resolveCompletionItem(suggestion, token) {
      const origin = origins.get(suggestion)
      if (!origin || origin.client.isClosed || !origin.client.capabilities.completionProvider?.resolveProvider)
        return suggestion
      try {
        const resolved = await origin.client.resolveCompletion(origin.item, token)
        return {
          ...suggestion,
          detail: resolved.detail ?? suggestion.detail,
          documentation: documentation(resolved.documentation) ?? suggestion.documentation,
          additionalTextEdits:
            resolved.additionalTextEdits?.map((e) => ({ range: toRange(e.range), text: e.newText })) ??
            suggestion.additionalTextEdits,
        }
      } catch {
        return suggestion
      }
    },
  }

  const hover: Monaco.languages.HoverProvider = {
    async provideHover(model, position, token) {
      const at = served(model)
      if (!at?.client.capabilities.hoverProvider) return null
      try {
        const result = await at.client.hover(at.uri, toPosition(position), token)
        if (!result) return null
        const contents = hoverContents(result.contents)
        if (!contents.length) return null
        return { contents, range: result.range && toRange(result.range) }
      } catch {
        return null
      }
    },
  }

  const definition: Monaco.languages.DefinitionProvider = {
    async provideDefinition(model, position, token) {
      const at = served(model)
      if (!at?.client.capabilities.definitionProvider) return null
      try {
        return toLocations(monaco, at, await at.client.definition(at.uri, toPosition(position), token))
      } catch {
        return null
      }
    },
  }

  const references: Monaco.languages.ReferenceProvider = {
    async provideReferences(model, position, context, token) {
      const at = served(model)
      if (!at?.client.capabilities.referencesProvider) return null
      try {
        const found = await at.client.references(at.uri, toPosition(position), context.includeDeclaration, token)
        return toLocations(monaco, at, found)
      } catch {
        return null
      }
    },
  }

  return { completion, hover, definition, references }
}

const registered = new Set<string>()

function ensureFeatures(monaco: MonacoApi, languageId: string) {
  if (registered.has(languageId)) return
  registered.add(languageId)
  const p = providers(monaco)
  monaco.languages.registerCompletionItemProvider(languageId, p.completion)
  monaco.languages.registerHoverProvider(languageId, p.hover)
  monaco.languages.registerDefinitionProvider(languageId, p.definition)
  monaco.languages.registerReferenceProvider(languageId, p.references)
}

// ---------------------------------------------------------------- opening another file

const editorLinks = new WeakMap<Monaco.editor.ICodeEditor, LanguageLink>()
let openerRegistered = false

/**
 * Go to definition, or a reference picked in the peek, in another file of the editor's task:
 * that file opens where the editor's link says, at the line. Anything else (the same file, a
 * file outside the checkout) is left to Monaco, which moves within the same file and does
 * nothing for a file it has no model of.
 */
export function openerFor(monaco: MonacoApi, editor: Monaco.editor.ICodeEditor, link: LanguageLink): () => void {
  editorLinks.set(editor, link)
  if (!openerRegistered) {
    openerRegistered = true
    monaco.editor.registerEditorOpener({
      openCodeEditor(source, resource, selectionOrPosition) {
        const own = editorLinks.get(source)
        const place = placeOf(resource)
        if (!own || !place || !sameScope(place.scope, own.scope)) return false
        if (source.getModel()?.uri.toString() === resource.toString()) return false
        const line = !selectionOrPosition
          ? 1
          : 'startLineNumber' in selectionOrPosition
            ? selectionOrPosition.startLineNumber
            : selectionOrPosition.lineNumber
        own.open(place.path, line)
        return true
      },
    })
  }
  return () => {
    if (editorLinks.get(editor) === link) editorLinks.delete(editor)
  }
}
