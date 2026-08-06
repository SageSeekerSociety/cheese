import { Node, mergeAttributes } from '@tiptap/core';
import { Node as Node$1 } from '@tiptap/pm/model';
import Suggestion from '@tiptap/suggestion';
import { PluginKey } from '@tiptap/pm/state';

/**
 * Returns the suggestion options for a trigger of the Mention extension. These
 * options are used to create a `Suggestion` ProseMirror plugin. Each plugin lets
 * you define a different trigger that opens the Mention menu. For example,
 * you can define a `@` trigger to mention users and a `#` trigger to mention
 * tags.
 *
 * @param param0 The configured options for the suggestion
 * @returns
 */
function getSuggestionOptions({ editor: tiptapEditor, overrideSuggestionOptions, extensionName, char = '@', }) {
    const pluginKey = new PluginKey();
    return {
        editor: tiptapEditor,
        char,
        pluginKey,
        command: ({ editor, range, props }) => {
            var _a, _b, _c;
            // increase range.to by one when the next node is of type "text"
            // and starts with a space character
            const nodeAfter = editor.view.state.selection.$to.nodeAfter;
            const overrideSpace = (_a = nodeAfter === null || nodeAfter === void 0 ? void 0 : nodeAfter.text) === null || _a === void 0 ? void 0 : _a.startsWith(' ');
            if (overrideSpace) {
                range.to += 1;
            }
            editor
                .chain()
                .focus()
                .insertContentAt(range, [
                {
                    type: extensionName,
                    attrs: { ...props, mentionSuggestionChar: char },
                },
                {
                    type: 'text',
                    text: ' ',
                },
            ])
                .run();
            // get reference to `window` object from editor element, to support cross-frame JS usage
            (_c = (_b = editor.view.dom.ownerDocument.defaultView) === null || _b === void 0 ? void 0 : _b.getSelection()) === null || _c === void 0 ? void 0 : _c.collapseToEnd();
        },
        allow: ({ state, range }) => {
            const $from = state.doc.resolve(range.from);
            const type = state.schema.nodes[extensionName];
            const allow = !!$from.parent.type.contentMatch.matchType(type);
            return allow;
        },
        ...overrideSuggestionOptions,
    };
}

/**
 * Returns the suggestions for the mention extension.
 *
 * @param options The extension options
 * @returns the suggestions
 */
function getSuggestions(options) {
    return (options.options.suggestions.length ? options.options.suggestions : [options.options.suggestion]).map(suggestion => getSuggestionOptions({
        // @ts-ignore `editor` can be `undefined` when converting the document to HTML with the HTML utility
        editor: options.editor,
        overrideSuggestionOptions: suggestion,
        extensionName: options.name,
        char: suggestion.char,
    }));
}
/**
 * Returns the suggestion options of the mention that has a given character trigger. If not
 * found, it returns the first suggestion.
 *
 * @param options The extension options
 * @param char The character that triggers the mention
 * @returns The suggestion options
 */
function getSuggestionFromChar(options, char) {
    const suggestions = getSuggestions(options);
    const suggestion = suggestions.find(s => s.char === char);
    if (suggestion) {
        return suggestion;
    }
    if (suggestions.length) {
        return suggestions[0];
    }
    return null;
}
/**
 * This extension allows you to insert mentions into the editor.
 * @see https://www.tiptap.dev/api/extensions/mention
 */
const Mention = Node.create({
    name: 'mention',
    priority: 101,
    addOptions() {
        return {
            HTMLAttributes: {},
            renderText({ node, suggestion }) {
                var _a, _b;
                return `${(_a = suggestion === null || suggestion === void 0 ? void 0 : suggestion.char) !== null && _a !== void 0 ? _a : '@'}${(_b = node.attrs.label) !== null && _b !== void 0 ? _b : node.attrs.id}`;
            },
            deleteTriggerWithBackspace: false,
            renderHTML({ options, node, suggestion }) {
                var _a, _b;
                return [
                    'span',
                    mergeAttributes(this.HTMLAttributes, options.HTMLAttributes),
                    `${(_a = suggestion === null || suggestion === void 0 ? void 0 : suggestion.char) !== null && _a !== void 0 ? _a : '@'}${(_b = node.attrs.label) !== null && _b !== void 0 ? _b : node.attrs.id}`,
                ];
            },
            suggestions: [],
            suggestion: {},
        };
    },
    group: 'inline',
    inline: true,
    selectable: false,
    atom: true,
    addAttributes() {
        return {
            id: {
                default: null,
                parseHTML: element => element.getAttribute('data-id'),
                renderHTML: attributes => {
                    if (!attributes.id) {
                        return {};
                    }
                    return {
                        'data-id': attributes.id,
                    };
                },
            },
            label: {
                default: null,
                parseHTML: element => element.getAttribute('data-label'),
                renderHTML: attributes => {
                    if (!attributes.label) {
                        return {};
                    }
                    return {
                        'data-label': attributes.label,
                    };
                },
            },
            // When there are multiple types of mentions, this attribute helps distinguish them
            mentionSuggestionChar: {
                default: '@',
                parseHTML: element => element.getAttribute('data-mention-suggestion-char'),
                renderHTML: attributes => {
                    return {
                        'data-mention-suggestion-char': attributes.mentionSuggestionChar,
                    };
                },
            },
        };
    },
    parseHTML() {
        return [
            {
                tag: `span[data-type="${this.name}"]`,
            },
        ];
    },
    renderHTML({ node, HTMLAttributes }) {
        const suggestion = getSuggestionFromChar(this, node.attrs.mentionSuggestionChar);
        if (this.options.renderLabel !== undefined) {
            console.warn('renderLabel is deprecated use renderText and renderHTML instead');
            return [
                'span',
                mergeAttributes({ 'data-type': this.name }, this.options.HTMLAttributes, HTMLAttributes),
                this.options.renderLabel({
                    options: this.options,
                    node,
                    suggestion,
                }),
            ];
        }
        const mergedOptions = { ...this.options };
        mergedOptions.HTMLAttributes = mergeAttributes({ 'data-type': this.name }, this.options.HTMLAttributes, HTMLAttributes);
        const html = this.options.renderHTML({
            options: mergedOptions,
            node,
            suggestion,
        });
        if (typeof html === 'string') {
            return [
                'span',
                mergeAttributes({ 'data-type': this.name }, this.options.HTMLAttributes, HTMLAttributes),
                html,
            ];
        }
        return html;
    },
    renderText({ node }) {
        const args = {
            options: this.options,
            node,
            suggestion: getSuggestionFromChar(this, node.attrs.mentionSuggestionChar),
        };
        if (this.options.renderLabel !== undefined) {
            console.warn('renderLabel is deprecated use renderText and renderHTML instead');
            return this.options.renderLabel(args);
        }
        return this.options.renderText(args);
    },
    addKeyboardShortcuts() {
        return {
            Backspace: () => this.editor.commands.command(({ tr, state }) => {
                let isMention = false;
                const { selection } = state;
                const { empty, anchor } = selection;
                if (!empty) {
                    return false;
                }
                state.doc.nodesBetween(anchor - 1, anchor, (node, pos) => {
                    if (node.type.name === this.name) {
                        isMention = true;
                        tr.insertText(this.options.deleteTriggerWithBackspace ? '' : this.options.suggestion.char || '', pos, pos + node.nodeSize);
                        return false;
                    }
                });
                // Store node and position for later use
                let mentionNode = new Node$1();
                let mentionPos = 0;
                state.doc.nodesBetween(anchor - 1, anchor, (node, pos) => {
                    if (node.type.name === this.name) {
                        isMention = true;
                        mentionNode = node;
                        mentionPos = pos;
                        return false;
                    }
                });
                if (isMention) {
                    tr.insertText(this.options.deleteTriggerWithBackspace ? '' : mentionNode.attrs.mentionSuggestionChar, mentionPos, mentionPos + mentionNode.nodeSize);
                }
                return isMention;
            }),
        };
    },
    addProseMirrorPlugins() {
        // Create a plugin for each suggestion configuration
        return getSuggestions(this).map(Suggestion);
    },
});

export { Mention, Mention as default };
//# sourceMappingURL=index.js.map
