import type { BomItem, ChangeOperation } from "../../api/types";

export interface LineDraft {
  line_key: string;
  part_number: string;
  part_revision: string;
  description: string;
  quantity: string;
  unit: string;
  attributes: string;
  remove: boolean;
  isNew: boolean;
}

export function toDraft(i: BomItem): LineDraft {
  return {
    line_key: i.line_key,
    part_number: i.part_number,
    part_revision: i.part_revision,
    description: i.description,
    quantity: String(i.quantity),
    unit: i.unit,
    attributes: JSON.stringify(i.attributes_json),
    remove: false,
    isNew: false,
  };
}

export function emptyDraft(): LineDraft {
  return { line_key: "", part_number: "", part_revision: "A", description: "", quantity: "1", unit: "each", attributes: "{}", remove: false, isNew: true };
}

function stable(v: unknown): string {
  if (Array.isArray(v)) return `[${v.map(stable).join(",")}]`;
  if (v && typeof v === "object")
    return `{${Object.keys(v as object)
      .sort()
      .map((k) => `${JSON.stringify(k)}:${stable((v as Record<string, unknown>)[k])}`)
      .join(",")}}`;
  return JSON.stringify(v);
}

/** Field-level diff of edited drafts against the base revision. Returns operations or human-readable errors. */
export function buildChangeSet(base: BomItem[], drafts: LineDraft[]): { ops: ChangeOperation[]; errors: string[] } {
  const ops: ChangeOperation[] = [];
  const errors: string[] = [];
  const byKey = new Map(base.map((b) => [b.line_key, b]));
  for (const d of drafts) {
    const key = d.line_key.trim().toUpperCase();
    if (d.isNew && !key && !d.part_number.trim()) continue;
    if (!key) {
      errors.push("New line needs a line key");
      continue;
    }
    if (d.remove) {
      if (!d.isNew) ops.push({ line_key: key, operation: "REMOVE" });
      continue;
    }
    let attributes: unknown;
    try {
      attributes = JSON.parse(d.attributes || "{}");
      if (!attributes || typeof attributes !== "object" || Array.isArray(attributes)) throw new Error();
    } catch {
      errors.push(`${key}: attributes must be a JSON object`);
      continue;
    }
    const quantity = Number(d.quantity);
    if (!Number.isFinite(quantity) || quantity <= 0) {
      errors.push(`${key}: quantity must be a positive number`);
      continue;
    }
    const next = {
      part_number: d.part_number.trim(),
      part_revision: d.part_revision.trim(),
      description: d.description.trim(),
      quantity,
      unit: d.unit.trim(),
      attributes,
    };
    if (d.isNew) {
      if (byKey.has(key)) {
        errors.push(`${key}: line already exists`);
        continue;
      }
      ops.push({ line_key: key, operation: "ADD", new: next });
      continue;
    }
    const old = byKey.get(key);
    if (!old) continue;
    const delta: Record<string, unknown> = {};
    if (next.part_number !== old.part_number) delta.part_number = next.part_number;
    if (next.part_revision !== old.part_revision) delta.part_revision = next.part_revision;
    if (next.description !== old.description) delta.description = next.description;
    if (next.quantity !== old.quantity) delta.quantity = next.quantity;
    if (next.unit !== old.unit) delta.unit = next.unit;
    if (stable(next.attributes) !== stable(old.attributes_json)) delta.attributes = next.attributes;
    if (Object.keys(delta).length) ops.push({ line_key: key, operation: "UPDATE", new: delta });
  }
  return { ops, errors };
}
