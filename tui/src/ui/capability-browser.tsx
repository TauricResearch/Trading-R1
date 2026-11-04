// Copyright 2026 Tauric Research
// SPDX-License-Identifier: Apache-2.0

/**
 * Capability browser, the hierarchical drill-down opened by "Tools & skills…":
 * disciplines (+ Foundation / Extensions) -> subject domains -> tools & skills -> detail.
 * Each level is a ListDialog; `esc` (onCancel) walks back up via the captured `back`
 * closure, and closing a detail returns to its item list. Pure navigation over an
 * already-fetched catalog; app.tsx owns the fetch and passes it in.
 */
import type { CapabilitiesCatalog, CapabilityItem, DisciplineGroup } from "../lib/api"
import {
  capabilityDetail,
  disciplineOptions,
  domainItems,
  domainOptions,
  itemOptions,
} from "../lib/capabilities"
import { layout } from "../theme"
import type { DialogApi } from "./dialog"
import { InfoDialog } from "./info-dialog"
import { ListDialog } from "./list-dialog"

export function openCapabilityBrowser(dialog: DialogApi, cat: CapabilitiesCatalog): void {
  const openItem = (item: CapabilityItem, back: () => void) =>
    dialog.show(() => (
      <InfoDialog
        title={item.display_name}
        lines={capabilityDetail(item)}
        onClose={back}
        width={layout.width.lg}
        escape="back"
      />
    ))

  // A leaf list of tools/skills; selecting one opens detail, whose close returns here.
  const openItems = (title: string, items: CapabilityItem[], back: () => void) => {
    const show = () =>
      dialog.show(() => (
        <ListDialog
          title={title}
          width={layout.width.lg}
          options={itemOptions(items)}
          confirm="open"
          escape="back"
          onCancel={back}
          onSelect={(v) => {
            const it = items.find((x) => `${x.kind}:${x.name}` === v)
            if (it) openItem(it, show)
          }}
        />
      ))
    show()
  }

  const openDiscipline = (disc: DisciplineGroup, back: () => void) => {
    const show = () =>
      dialog.show(() => (
        <ListDialog
          title={disc.title}
          width={layout.width.lg}
          options={domainOptions(disc)}
          confirm="open"
          escape="back"
          onCancel={back}
          onSelect={(v) => {
            const d = disc.domains.find((x) => `dom:${x.key}` === v)
            if (d) openItems(d.title, domainItems(d), show)
          }}
        />
      ))
    show()
  }

  const openTop = () =>
    dialog.show(() => (
      <ListDialog
        title="Tools & skills"
        width={layout.width.lg}
        options={disciplineOptions(cat)}
        confirm="open"
        escape="close"
        onCancel={() => dialog.close()}
        onSelect={(v) => {
          if (v === "foundation") openItems("Foundation", cat.foundation, openTop)
          else if (v === "extensions") openItems("Extensions", cat.extensions, openTop)
          else {
            const disc = cat.disciplines.find((d) => `disc:${d.key}` === v)
            if (disc) openDiscipline(disc, openTop)
          }
        }}
      />
    ))
  openTop()
}
