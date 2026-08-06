import { Node } from '@tiptap/core';
import { Node as ProseMirrorNode } from '@tiptap/pm/model';
export interface TaskItemOptions {
    /**
     * A callback function that is called when the checkbox is clicked while the editor is in readonly mode.
     * @param node The prosemirror node of the task item
     * @param checked The new checked state
     * @returns boolean
     */
    onReadOnlyChecked?: (node: ProseMirrorNode, checked: boolean) => boolean;
    /**
     * Controls whether the task items can be nested or not.
     * @default false
     * @example true
     */
    nested: boolean;
    /**
     * HTML attributes to add to the task item element.
     * @default {}
     * @example { class: 'foo' }
     */
    HTMLAttributes: Record<string, any>;
    /**
     * The node type for taskList nodes
     * @default 'taskList'
     * @example 'myCustomTaskList'
     */
    taskListTypeName: string;
    /**
     * Accessibility options for the task item.
     * @default {}
     * @example
     * ```js
     * {
     *   checkboxLabel: (node) => `Task item: ${node.textContent || 'empty task item'}`
     * }
     */
    a11y?: {
        checkboxLabel?: (node: ProseMirrorNode, checked: boolean) => string;
    };
}
/**
 * Matches a task item to a - [ ] on input.
 */
export declare const inputRegex: RegExp;
/**
 * This extension allows you to create task items.
 * @see https://www.tiptap.dev/api/nodes/task-item
 */
export declare const TaskItem: Node<TaskItemOptions, any>;
//# sourceMappingURL=task-item.d.ts.map