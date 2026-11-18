// The tag input's state and the pure functions that change it. Each change takes the current
// state (and the field's rules, when it needs them) and returns the next one, so several changes
// in one event compose.
import { gettext } from './i18n';

export interface Tag {
  value: string;
  /** Why `validate` rejected the value, or null when it is valid. */
  error: string | null;
}

/** The tag being edited in place. */
export interface Editing {
  index: number;
  /** The tag's value before the edit, restored by Escape. */
  original: string;
  text: string;
}

export interface State {
  tags: Tag[];
  /** Text typed into the input that is not a tag yet. */
  text: string;
  editing: Editing | null;
  /** Every tag is selected with Ctrl/Cmd+A. */
  allSelected: boolean;
}

/** `true` for a valid entry; `false` or an error message for an invalid one. */
export type Validate = (value: string) => boolean | string;

export interface Rules {
  normalize: (value: string) => string;
  validate: Validate;
  /** Matches the separators between entries. */
  pattern: RegExp;
}

const LINE_BREAK = /\r?\n/;

export function initialState(rules: Rules, storedValue: string): State {
  const empty = { tags: [], text: '', editing: null, allSelected: false };
  return addEntries(empty, rules, parse(storedValue, LINE_BREAK));
}

// Changes.

export function addEntries(
  state: State,
  rules: Rules,
  entries: string[],
): State {
  const newTags = createTags(entries, new Set(values(state)), rules);
  return { ...state, tags: [...state.tags, ...newTags] };
}

/** Turns the typed text into tags. */
export function commitText(state: State, rules: Rules): State {
  return { ...addEntries(state, rules, [clean(state.text)]), text: '' };
}

/**
 * Takes the input's raw text. Text before a separator becomes tags; the rest stays typed. Mobile
 * keyboards and autofill can insert a separator without a keydown for it.
 */
export function setText(state: State, rules: Rules, rawText: string): State {
  const parts = rawText.split(rules.pattern);
  const text = parts.pop() ?? '';
  const entries = parts.map(clean).filter(Boolean);
  return { ...addEntries(state, rules, entries), text };
}

/** Like pasting over selected text, a paste replaces the tags selected with Ctrl/Cmd+A. */
export function paste(state: State, rules: Rules, text: string): State {
  const base = state.allSelected ? clear(state) : state;
  return { ...addEntries(base, rules, parse(text, rules.pattern)), text: '' };
}

export function removeTag(state: State, value: string): State {
  const index = state.tags.findIndex((tag) => tag.value === value);
  if (index < 0) return state;
  return {
    ...state,
    tags: state.tags.filter((tag) => tag.value !== value),
    editing: state.editing?.index === index ? null : state.editing,
  };
}

export function clear(state: State): State {
  return { ...state, tags: [], text: '', editing: null, allSelected: false };
}

export function setAllSelected(state: State, allSelected: boolean): State {
  return state.allSelected === allSelected ? state : { ...state, allSelected };
}

/** Starts editing the tag in place, after saving any typed text or other edit. */
export function editTag(state: State, rules: Rules, value: string): State {
  if (state.editing?.original === value) return state;
  const next = commitText(finishEdit(state, rules), rules);
  const index = next.tags.findIndex((tag) => tag.value === value);
  if (index < 0) return next;
  return { ...next, editing: { index, original: value, text: value } };
}

export function setEditText(state: State, text: string): State {
  if (!state.editing) return state;
  return { ...state, editing: { ...state.editing, text } };
}

export interface FinishEditOptions {
  /** Restore the tag's original value. */
  cancel?: boolean;
  /** Save this text instead of the edited text. */
  text?: string;
}

/**
 * Saves the tag being edited, in its place: no text removes it, a duplicate of another tag is
 * dropped, and text with separators in it becomes several tags.
 */
export function finishEdit(
  state: State,
  rules: Rules,
  { cancel = false, text }: FinishEditOptions = {},
): State {
  if (!state.editing) return state;
  const { index, original } = state.editing;
  const entries = cancel
    ? [original]
    : parse(text ?? state.editing.text, rules.pattern);
  const others = new Set(values(state).filter((_, i) => i !== index));
  const replacements = createTags(entries, others, rules);
  return {
    ...state,
    tags: [
      ...state.tags.slice(0, index),
      ...replacements,
      ...state.tags.slice(index + 1),
    ],
    editing: null,
  };
}

/** Saves everything in progress, as the form submits. */
export function finishAll(state: State, rules: Rules): State {
  return commitText(finishEdit(state, rules), rules);
}

// Reading state.

export function values(state: State): string[] {
  return state.tags.map((tag) => tag.value);
}

export function isValid(state: State): boolean {
  return state.tags.every((tag) => tag.error === null);
}

/** One entry per line, including text typed but not yet a tag. */
export function storedValue(state: State): string {
  const pending = clean(state.text);
  return [...values(state), ...(pending ? [pending] : [])].join('\n');
}

// Text helpers.

/**
 * Tags for the entries, skipping empty entries and values already in `existing` or earlier in
 * `entries`.
 */
function createTags(
  entries: string[],
  existing: Set<string>,
  { normalize, validate }: Rules,
): Tag[] {
  const seen = new Set(existing);
  const tags: Tag[] = [];
  for (const entry of entries) {
    const value = clean(normalize(entry));
    if (!value || seen.has(value)) continue;
    seen.add(value);
    tags.push({ value, error: validationError(validate(value)) });
  }
  return tags;
}

function validationError(result: boolean | string): string | null {
  if (result === true) return null;
  if (typeof result === 'string') return result;
  return gettext('Invalid entry');
}

function parse(text: string, pattern: RegExp): string[] {
  return text.split(pattern).map(clean).filter(Boolean);
}

/** Entries never contain line breaks, because a line break separates entries in the stored value. */
function clean(text: string): string {
  return text.replace(/\s+/g, ' ').trim();
}

/**
 * A pattern matching any of the separator keys. A line break always separates entries: an entry
 * can't hold one, and without this a pasted line break would be collapsed into a space and joined
 * onto the next entry.
 */
export function separatorPattern(separators: string[]): RegExp {
  const parts = separators
    .filter((separator) => separator !== 'Enter')
    .map((separator) => (separator === ' ' ? '\\s' : escapeRegExp(separator)));
  return new RegExp([...parts, '\\r?\\n'].join('|'));
}

function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}
