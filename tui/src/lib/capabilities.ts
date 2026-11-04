// Copyright 2026 Tauric Research
// SPDX-License-Identifier: Apache-2.0

/**
 * Capability browser: a hierarchical drill-down over the `/capabilities` catalog:
 * disciplines (+ Foundation / Extensions) -> subject domains -> tools & skills -> detail.
 * Each level is a plain ListDialog of selectable rows (no interleaved headers); esc walks
 * back up. These builders are pure so they unit-test without the UI; app.tsx wires the
 * navigation. Row names stay ASCII.
 */
import type { CapabilitiesCatalog, CapabilityItem, DisciplineGroup, DomainGroup } from "./api"
import type { ListOption } from "../ui/list-dialog"

/** "6 tools, 5 skills" / "1 tool": the count hint on a domain row. */
export function countLabel(nTools: number, nSkills: number): string {
  const parts: string[] = []
  if (nTools) parts.push(`${nTools} tool${nTools === 1 ? "" : "s"}`)
  if (nSkills) parts.push(`${nSkills} skill${nSkills === 1 ? "" : "s"}`)
  return parts.join(", ") || "empty"
}

/** Top level: Foundation, each discipline (described by its domains), then Extensions
 *  (only if any). Values: "foundation" | "extensions" | `disc:<key>`. */
export function disciplineOptions(cat: CapabilitiesCatalog): ListOption[] {
  const options: ListOption[] = [
    {
      name: "Foundation",
      description: "Always-on web & core tools",
      value: "foundation",
    },
  ]
  for (const disc of cat.disciplines) {
    options.push({
      name: disc.title,
      description: disc.domains.map((d) => d.title).join(", "),
      value: `disc:${disc.key}`,
    })
  }
  if (cat.extensions.length) {
    options.push({
      name: "Extensions",
      description: "Tools from outside the built-in groups",
      value: "extensions",
    })
  }
  return options
}

/** Second level: the subject domains within a discipline. Value `dom:<key>`. */
export function domainOptions(disc: DisciplineGroup): ListOption[] {
  return disc.domains.map((d) => ({
    name: d.title,
    description: countLabel(d.tools.length, d.skills.length),
    value: `dom:${d.key}`,
  }))
}

/** Leaf level: the tools + skills of a domain (or Foundation/Extensions). The description
 *  is the item's own `description`, taken from the tool/skill schema as written.
 *  Value `<kind>:<name>` (kind disambiguates a tool and skill that share a name). */
export function itemOptions(items: CapabilityItem[]): ListOption[] {
  return items.map((it) => ({
    name: it.display_name,
    description: it.description,
    value: `${it.kind}:${it.name}`,
  }))
}

/** All tools then skills of a domain, in one list. */
export function domainItems(d: DomainGroup): CapabilityItem[] {
  return [...d.tools, ...d.skills]
}

/** Detail lines for one capability, shown in an InfoDialog when its row is selected. */
export function capabilityDetail(it: CapabilityItem): string[] {
  const where = it.domain ? `${it.family} / ${it.domain}` : it.family
  const lines = [`${it.kind === "skill" ? "Skill" : "Tool"}  (${where})`, "", it.description]
  if (it.kind === "skill" && it.requires_tools?.length) {
    lines.push("", `Loads tools: ${it.requires_tools.join(", ")}`)
  }
  lines.push("")
  if (it.tier === "core") lines.push("Always available (no load needed).")
  else if (it.kind === "skill") lines.push(`Load with:  load_skill ${it.name}`)
  else lines.push(`Load with:  load_tool ${it.name}`)
  return lines
}
