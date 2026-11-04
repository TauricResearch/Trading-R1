// Copyright 2026 Tauric Research
// SPDX-License-Identifier: Apache-2.0

/**
 * Layout: the geometry tokens, companion to `theme`'s color tokens.
 *
 * Components use these, never raw numbers. A value that expresses a system (how wide
 * dialogs are, how far content sits from a border) belongs here; a positional one-off
 * (a spacer row, a max text height) stays local to its component.
 */

export const layout = {
  /** Every bordered element in the app shares one border style. */
  border: "rounded",

  /**
   * Modal widths: a three-step scale picked by content density.
   */
  width: {
    sm: 56, // terse: a short message, a list of short command titles
    md: 64, // key/label tables, session + model lists, a single prompt field
    lg: 72, // densest: a rich list (tools/skills), a detail card, a question + field
  },

  /**
   * Inset scale. Terminal cells are about twice as tall as they are wide, so an
   * inset of two columns and one row reads as the same visual gap.
   */
  pad: {
    modal: { x: 2, y: 1 }, // floating dialogs + overlays
    docked: { x: 1, y: 1 }, // panels docked in the workspace (transcript, rails)
    field: 1, // inside a text-entry border
  },
} as const
