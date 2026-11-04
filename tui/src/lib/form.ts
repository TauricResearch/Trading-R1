// Copyright 2026 Tauric Research
// SPDX-License-Identifier: Apache-2.0

/**
 * Form state: a list of fields (as a destination kind declares them) turned into what the
 * form dialog edits and what it sends. Pure: the dialog owns the keys and the rendering; the
 * server owns the real validation, and this only catches what a form can say at once (a blank
 * required field, text where a number goes) so the owner is told before a round trip.
 */
export interface FieldSpec {
  name: string
  label: string
  type: "text" | "int" | "choice" | "list" | "secret" | "path"
  required: boolean
  default: unknown
  choices: string[]
  minimum: number | null
  maximum: number | null
  help: string
  example: string
}

/** The fields to ask: all of them, or with a preset only what it leaves to the owner (the required
 *  settings and the secrets it does not fill). */
export function askedFields(fields: FieldSpec[], preset: Record<string, unknown>): FieldSpec[] {
  if (!Object.keys(preset).length) return fields
  return fields.filter((f) => f.type === "secret" || (f.required && !(f.name in preset)))
}

export interface Form {
  fields: FieldSpec[]
  values: Record<string, string> // what is typed; a list is comma-separated
  secretNames: Record<string, string> // a secret field -> the credential it names
  focus: number
  errors: Record<string, string>
}

/** The text a field starts with: an existing setting, else a preset, else its default. A secret
 *  field starts empty (its value is never read back); the credential name is kept apart. */
export function newForm(
  fields: FieldSpec[],
  existing: Record<string, unknown> = {},
  preset: Record<string, unknown> = {},
): Form {
  const values: Record<string, string> = {}
  const secretNames: Record<string, string> = {}
  for (const f of fields) {
    const given = existing[f.name] ?? preset[f.name] ?? f.default
    if (f.type === "secret") {
      secretNames[f.name] = typeof given === "string" ? given : ""
      values[f.name] = ""
    } else if (given == null) {
      values[f.name] = ""
    } else {
      values[f.name] = Array.isArray(given) ? given.join(", ") : String(given)
    }
  }
  return { fields, values, secretNames, focus: 0, errors: {} }
}

export function setValue(form: Form, name: string, value: string): Form {
  const { [name]: _gone, ...errors } = form.errors
  return { ...form, values: { ...form.values, [name]: value }, errors }
}

export function moveFocus(form: Form, step: 1 | -1): Form {
  const n = form.fields.length
  return { ...form, focus: (form.focus + step + n) % n }
}

/** A choice field steps to its next (or previous) value. */
export function cycleChoice(form: Form, name: string, step: 1 | -1): Form {
  const f = form.fields.find((x) => x.name === name)
  if (!f || f.type !== "choice" || !f.choices.length) return form
  const at = f.choices.indexOf(form.values[name])
  const next = f.choices[(at + step + f.choices.length) % f.choices.length]
  return setValue(form, name, next)
}

/** Entries are separated by commas; a comma inside quotes or <angle brackets> belongs to the
 *  entry (`"Doe, Jane" <jane@example.com>` is one address). */
export function splitList(text: string): string[] {
  const out: string[] = []
  let current = ""
  let quoted = false
  let angled = false
  for (const ch of text) {
    if (ch === '"') quoted = !quoted
    else if (!quoted && ch === "<") angled = true
    else if (!quoted && ch === ">") angled = false
    if (ch === "," && !quoted && !angled) {
      out.push(current)
      current = ""
    } else {
      current += ch
    }
  }
  out.push(current)
  return out.map((x) => x.trim()).filter(Boolean)
}

/** What a form can check on its own. Returns the errors by field name (empty when sound). A
 *  secret may be left blank only when it is already set (`configured`). */
export function check(form: Form, configured: Record<string, boolean> = {}): Record<string, string> {
  const errors: Record<string, string> = {}
  for (const f of form.fields) {
    const text = form.values[f.name].trim()
    if (f.type === "secret") {
      if (f.required && !text && !configured[f.name]) errors[f.name] = "required"
      continue
    }
    if (!text) {
      if (f.required) errors[f.name] = "required"
      continue
    }
    if (f.type === "int") {
      const n = Number(text)
      if (!/^-?\d+$/.test(text) || !Number.isSafeInteger(n)) errors[f.name] = "a whole number"
      else if ((f.minimum != null && n < f.minimum) || (f.maximum != null && n > f.maximum))
        errors[f.name] = `between ${f.minimum} and ${f.maximum}`
    }
    if (f.type === "list" && !splitList(text).length) errors[f.name] = "required"
  }
  return errors
}

export interface Payload {
  settings: Record<string, unknown>
  secrets: Record<string, string>
}

/** The request body: settings by type (a blank text is left out, so the server's default
 *  applies), a secret's credential name in the settings and its typed value in `secrets`. */
export function toPayload(form: Form): Payload {
  const settings: Record<string, unknown> = {}
  const secrets: Record<string, string> = {}
  for (const f of form.fields) {
    const text = form.values[f.name].trim()
    if (f.type === "secret") {
      if (form.secretNames[f.name]) settings[f.name] = form.secretNames[f.name]
      if (text) secrets[f.name] = form.values[f.name]
    } else if (!text) {
      continue
    } else if (f.type === "int") {
      settings[f.name] = Number(text)
    } else if (f.type === "list") {
      settings[f.name] = splitList(text)
    } else {
      settings[f.name] = text
    }
  }
  return { settings, secrets }
}

/** The server's 422 text names fields as `name: problem; name: problem`; attach each to its
 *  field, and anything else to `_`. */
export function serverErrors(detail: string, fields: FieldSpec[]): Record<string, string> {
  const errors: Record<string, string> = {}
  const names = new Set(fields.map((f) => f.name))
  for (const part of detail
    .split(";")
    .map((x) => x.trim())
    .filter(Boolean)) {
    const m = /^([a-z_]+): (.*)$/.exec(part)
    if (m && names.has(m[1])) errors[m[1]] = m[2]
    else errors._ = [errors._, part].filter(Boolean).join("; ")
  }
  return errors
}

/** Typing into a secret field: a secret never enters an input widget (which would draw it), so
 *  the dialog edits the value here and draws dots. */
export function typeSecret(form: Form, name: string, text: string): Form {
  const clean = text.replace(/[\r\n\0]/g, "")
  return clean ? setValue(form, name, form.values[name] + clean) : form
}

export function eraseSecret(form: Form, name: string, all = false): Form {
  return setValue(form, name, all ? "" : Array.from(form.values[name]).slice(0, -1).join(""))
}

/** A secret shown as dots and a length, never its characters. */
export function masked(text: string): string {
  return text
    ? "•".repeat(Math.min(text.length, 24)) + (text.length > 24 ? "…" : "") + ` (${text.length} characters)`
    : ""
}
