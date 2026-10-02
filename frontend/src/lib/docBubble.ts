// A formatting change the selection bar makes carries this meta. The bar
// closes when the text under it changes, but a change it made itself leaves
// the same passage selected, so it stays (components/panels/doc/DocBubble.vue).
export const BUBBLE_META = 'docBubble'

/** Where a selection is, for a comment or the AI teammate: the node it is
 *  anchored to (null when the document on screen and the stored one disagree)
 *  and the selected text. */
export interface SelectionTarget {
  anchorId: string | null
  quote: string
}
