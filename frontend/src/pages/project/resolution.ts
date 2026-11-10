import type { Artifact, BomItem } from "../../api/types";

type Ref = { part_number?: string; part_revision?: string; attributes?: Record<string, unknown> };

/**
 * Deterministic helper for the engineer: rewrites pinned BOM references (and pinned artifact revisions) in a
 * work-instruction / QC draft from the current baseline. The engineer still reviews and submits; the server re-validates.
 */
export function refreshReferences(content: Record<string, unknown>, bom: BomItem[], artifacts: Artifact[]): Record<string, unknown> {
  const byLine = new Map(bom.map((b) => [b.line_key, b]));
  const next: Record<string, unknown> = structuredClone(content);
  const refs = (next.references ?? {}) as Record<string, Ref>;
  for (const [line, ref] of Object.entries(refs)) {
    const item = byLine.get(line);
    if (!item) continue;
    ref.part_number = item.part_number;
    ref.part_revision = item.part_revision;
    if (ref.attributes) {
      for (const k of Object.keys(ref.attributes)) {
        if (k in item.attributes_json) ref.attributes[k] = item.attributes_json[k];
      }
    }
  }
  const pins = next.artifact_refs as Record<string, number> | undefined;
  if (pins) {
    const current = new Map(artifacts.filter((a) => a.is_current).map((a) => [a.node_key, a.revision]));
    for (const k of Object.keys(pins)) {
      const rev = current.get(k);
      if (rev) pins[k] = rev;
    }
  }
  return next;
}

export function lineKeyOf(nodeKey: string): string {
  return nodeKey.split(":", 2)[1] ?? "";
}
