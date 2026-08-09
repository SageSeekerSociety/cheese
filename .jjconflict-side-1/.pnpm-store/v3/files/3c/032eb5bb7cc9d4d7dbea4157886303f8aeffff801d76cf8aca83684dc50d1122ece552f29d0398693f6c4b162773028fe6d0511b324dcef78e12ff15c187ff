import { Node } from '@tiptap/core';
import type { DOMOutputSpec } from '@tiptap/pm/model';
import { Node as ProseMirrorNode } from '@tiptap/pm/model';
import type { SuggestionOptions } from '@tiptap/suggestion';
export interface MentionNodeAttrs {
    /**
     * The identifier for the selected item that was mentioned, stored as a `data-id`
     * attribute.
     */
    id: string | null;
    /**
     * The label to be rendered by the editor as the displayed text for this mentioned
     * item, if provided. Stored as a `data-label` attribute. See `renderLabel`.
     */
    label?: string | null;
    /**
     * The character that triggers the suggestion, stored as
     * `data-mention-suggestion-char` attribute.
     */
    mentionSuggestionChar?: string;
}
export interface MentionOptions<SuggestionItem = any, Attrs extends Record<string, any> = MentionNodeAttrs> {
    /**
     * The HTML attributes for a mention node.
     * @default {}
     * @example { class: 'foo' }
     */
    HTMLAttributes: Record<string, any>;
    /**
     * A function to render the label of a mention.
     * @deprecated use renderText and renderHTML instead
     * @param props The render props
     * @returns The label
     * @example ({ options, node }) => `${options.suggestion.char}${node.attrs.label ?? node.attrs.id}`
     */
    renderLabel?: (props: {
        options: MentionOptions<SuggestionItem, Attrs>;
        node: ProseMirrorNode;
        suggestion: SuggestionOptions | null;
    }) => string;
    /**
     * A function to render the text of a mention.
     * @param props The render props
     * @returns The text
     * @example ({ options, node }) => `${options.suggestion.char}${node.attrs.label ?? node.attrs.id}`
     */
    renderText: (props: {
        options: MentionOptions<SuggestionItem, Attrs>;
        node: ProseMirrorNode;
        suggestion: SuggestionOptions | null;
    }) => string;
    /**
     * A function to render the HTML of a mention.
     * @param props The render props
     * @returns The HTML as a ProseMirror DOM Output Spec
     * @example ({ options, node }) => ['span', { 'data-type': 'mention' }, `${options.suggestion.char}${node.attrs.label ?? node.attrs.id}`]
     */
    renderHTML: (props: {
        options: MentionOptions<SuggestionItem, Attrs>;
        node: ProseMirrorNode;
        suggestion: SuggestionOptions | null;
    }) => DOMOutputSpec;
    /**
     * Whether to delete the trigger character with backspace.
     * @default false
     */
    deleteTriggerWithBackspace: boolean;
    /**
     * The suggestion options, when you want to support multiple triggers.
     *
     * With this parameter, you can define multiple types of mention. For example, you can use the `@` character
     * to mention users and the `#` character to mention tags.
     *
     * @default [{ char: '@', pluginKey: MentionPluginKey }]
     * @example [{ char: '@', pluginKey: MentionPluginKey }, { char: '#', pluginKey: new PluginKey('hashtag') }]
     */
    suggestions: Array<Omit<SuggestionOptions<SuggestionItem, Attrs>, 'editor'>>;
    /**
     * The suggestion options, when you want to support only one trigger. To support multiple triggers, use the
     * `suggestions` parameter instead.
     *
     * @default {}
     * @example { char: '@', pluginKey: MentionPluginKey, command: ({ editor, range, props }) => { ... } }
     */
    suggestion: Omit<SuggestionOptions<SuggestionItem, Attrs>, 'editor'>;
}
/**
 * This extension allows you to insert mentions into the editor.
 * @see https://www.tiptap.dev/api/extensions/mention
 */
export declare const Mention: Node<MentionOptions<any, MentionNodeAttrs>, any>;
//# sourceMappingURL=mention.d.ts.map