import { HighlightStyle } from '@codemirror/language';
import { tags as t } from '@lezer/highlight';

/* The code colours the Workbench draws with — the editor and a notebook's cells alike — taken from the theme's
   own tokens, so a file reads the same in every palette and in both modes. */

/** A theme token, faded to `pct` percent: for selections and highlights that must not hide the text. */
export const mix = (v: string, pct: number) => `color-mix(in oklab, var(${v}) ${pct}%, transparent)`;

export const colours = HighlightStyle.define([
  { tag: [t.keyword, t.controlKeyword, t.moduleKeyword, t.operatorKeyword, t.definitionKeyword, t.modifier], color: 'var(--os-violet)' },
  { tag: [t.string, t.special(t.string), t.regexp, t.character, t.inserted], color: 'var(--os-ok)' },
  { tag: [t.number, t.bool, t.null, t.atom, t.unit], color: 'var(--os-warn)' },
  { tag: [t.comment, t.lineComment, t.blockComment, t.docComment], color: 'var(--os-dim)', fontStyle: 'italic' },
  { tag: [t.function(t.variableName), t.function(t.propertyName), t.definition(t.function(t.variableName)), t.macroName], color: 'var(--os-info)' },
  { tag: [t.typeName, t.className, t.namespace, t.definition(t.typeName), t.self], color: 'var(--os-brand)' },
  { tag: [t.tagName, t.deleted, t.invalid], color: 'var(--os-danger)' },
  { tag: [t.attributeName, t.propertyName, t.labelName], color: 'var(--os-ink-2)' },
  { tag: [t.operator, t.punctuation, t.bracket, t.meta, t.processingInstruction], color: 'var(--os-soft)' },
  { tag: t.heading, color: 'var(--os-brand)', fontWeight: '600' },
  { tag: t.strong, fontWeight: '600' },
  { tag: t.emphasis, fontStyle: 'italic' },
  { tag: t.link, color: 'var(--os-info)', textDecoration: 'underline' },
  { tag: t.strikethrough, textDecoration: 'line-through' },
]);
