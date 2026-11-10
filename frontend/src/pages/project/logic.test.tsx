import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import type { Artifact, BomItem, Candidate, Graph, Project } from "../../api/types";
import { layoutGraph } from "../../components/GraphView";
import { buildChangeSet, emptyDraft, toDraft } from "./changeSet";
import { buildDecisions, canAcceptAsIs } from "./ExtractionReviewPage";
import { ReadinessBanner } from "./ProjectLayout";
import { refreshReferences } from "./resolution";

const item = (line_key: string, extra: Partial<BomItem> = {}): BomItem => ({
  line_key,
  part_number: `${line_key}-P`,
  part_revision: "A",
  description: "Synthetic",
  quantity: 1,
  unit: "each",
  attributes_json: { voltage_v: 24 },
  provenance_json: {},
  ...extra,
});

describe("buildChangeSet", () => {
  const base = [item("ACTUATOR_MAIN", { part_number: "ACT-100", attributes_json: { voltage_v: 24, mount_pattern: "M4-40" } }), item("SENSOR")];

  it("emits no operations when nothing changed", () => {
    expect(buildChangeSet(base, base.map(toDraft))).toEqual({ ops: [], errors: [] });
  });

  it("emits field-level UPDATE with only changed fields; attribute key order is irrelevant", () => {
    const drafts = base.map(toDraft);
    drafts[0] = { ...drafts[0], part_number: "ACT-200", part_revision: "B", attributes: '{"mount_pattern":"M6-50","voltage_v":48}' };
    drafts[1] = { ...drafts[1], attributes: '{"voltage_v":24}' };
    const { ops, errors } = buildChangeSet(base, drafts);
    expect(errors).toEqual([]);
    expect(ops).toEqual([
      { line_key: "ACTUATOR_MAIN", operation: "UPDATE", new: { part_number: "ACT-200", part_revision: "B", attributes: { mount_pattern: "M6-50", voltage_v: 48 } } },
    ]);
  });

  it("handles ADD, REMOVE and validation errors", () => {
    const drafts = [...base.map(toDraft), { ...emptyDraft(), line_key: "lidar", part_number: "LDR-1" }];
    drafts[1] = { ...drafts[1], remove: true };
    const { ops } = buildChangeSet(base, drafts);
    expect(ops.map((o) => [o.line_key, o.operation])).toEqual([
      ["SENSOR", "REMOVE"],
      ["LIDAR", "ADD"],
    ]);
    const bad = base.map(toDraft);
    bad[0] = { ...bad[0], quantity: "-1" };
    bad[1] = { ...bad[1], attributes: "[1]" };
    expect(buildChangeSet(base, bad).errors).toHaveLength(2);
  });

  it("ignores an untouched blank new row", () => {
    expect(buildChangeSet(base, [...base.map(toDraft), emptyDraft()]).ops).toEqual([]);
  });
});

const cand = (over: Partial<Candidate> = {}): Candidate => ({
  id: "c1",
  kind: "BOM_LINE",
  field_path: "bom.ACTUATOR_MAIN",
  value_json: { line_key: "ACTUATOR_MAIN" },
  unit: "each",
  source_page: 2,
  source_section: null,
  source_excerpt: "BOM LINE ACTUATOR_MAIN",
  evidence_status: "VERIFIED",
  confidence_label: "HIGH",
  review_state: "PENDING",
  issues_json: [],
  corrected_value_json: null,
  correction_note: null,
  ...over,
});

describe("extraction review decisions", () => {
  it("refuses ACCEPT of unverified or conflicting candidates", () => {
    expect(canAcceptAsIs(cand())).toBe(true);
    expect(canAcceptAsIs(cand({ evidence_status: "NOT_FOUND" }))).toBe(false);
    expect(canAcceptAsIs(cand({ review_state: "CONFLICT" }))).toBe(false);
    expect(canAcceptAsIs(cand({ issues_json: [{ code: "MANDATORY_FIELD_MISSING" }] }))).toBe(false);
    const r = buildDecisions([cand({ evidence_status: "NOT_FOUND" })], { c1: { action: "ACCEPT", value: "", note: "" } });
    expect(r.decisions).toEqual([]);
    expect(r.errors[0]).toMatch(/cannot be accepted/);
  });

  it("requires a decision for every candidate and a note + valid JSON for edits", () => {
    expect(buildDecisions([cand()], {}).errors).toEqual(["bom.ACTUATOR_MAIN: no decision"]);
    expect(buildDecisions([cand()], { c1: { action: "EDIT", value: "{bad", note: "fine note" } }).errors[0]).toMatch(/JSON/);
    expect(buildDecisions([cand()], { c1: { action: "EDIT", value: "{}", note: "x" } }).errors[0]).toMatch(/note/);
    expect(buildDecisions([cand()], { c1: { action: "EDIT", value: '{"quantity":3}', note: "Customer confirmed" } }).decisions).toEqual([
      { candidate_id: "c1", action: "EDIT", value: { quantity: 3 }, note: "Customer confirmed" },
    ]);
  });
});

describe("refreshReferences", () => {
  it("re-pins part, revision, referenced attributes and artifact revisions from the current baseline", () => {
    const content = {
      document_code: "QC-ACT-01",
      references: { ACTUATOR_MAIN: { part_number: "ACT-100", part_revision: "A", attributes: { voltage_v: 24 } } },
      artifact_refs: { "WORK_INSTRUCTION:INSTALL_ACTUATOR": 1 },
    };
    const bom = [item("ACTUATOR_MAIN", { part_number: "ACT-200", part_revision: "B", attributes_json: { voltage_v: 48, mount_pattern: "M6-50" } })];
    const arts = [{ node_key: "WORK_INSTRUCTION:INSTALL_ACTUATOR", revision: 2, is_current: true } as Artifact];
    const out = refreshReferences(content, bom, arts);
    expect(out).toEqual({
      document_code: "QC-ACT-01",
      references: { ACTUATOR_MAIN: { part_number: "ACT-200", part_revision: "B", attributes: { voltage_v: 48 } } },
      artifact_refs: { "WORK_INSTRUCTION:INSTALL_ACTUATOR": 2 },
    });
    expect(content.references.ACTUATOR_MAIN.part_number).toBe("ACT-100");
  });
});

describe("layoutGraph", () => {
  const graph: Graph = {
    nodes: [
      { key: "READINESS_APPROVAL:PROJECT", node_type: "READINESS_APPROVAL", label: "Release", status: "BLOCKED", artifact_id: null, artifact_revision: null, open_exceptions: [] },
      { key: "BOM_LINE:ACTUATOR_MAIN", node_type: "BOM_LINE", label: "ACT", status: "CURRENT", artifact_id: null, artifact_revision: null, open_exceptions: [] },
      { key: "QC_PLAN:X", node_type: "QC_PLAN", label: "QC", status: "REVALIDATION_REQUIRED", artifact_id: null, artifact_revision: 1, open_exceptions: ["HIGH:QC_PLAN_STALE"] },
    ],
    edges: [
      { source: "BOM_LINE:ACTUATOR_MAIN", target: "QC_PLAN:X", dependency_type: "T", rule_code: "R" },
      { source: "QC_PLAN:X", target: "READINESS_APPROVAL:PROJECT", dependency_type: "T", rule_code: "R" },
    ],
  };

  it("places node types in left-to-right columns and highlights the propagation path", () => {
    const { nodes, edges } = layoutGraph(graph, new Set(["QC_PLAN:X"]), new Set(["BOM_LINE:ACTUATOR_MAIN"]));
    const x = Object.fromEntries(nodes.map((n) => [n.id, n.position.x]));
    expect(x["BOM_LINE:ACTUATOR_MAIN"]).toBeLessThan(x["QC_PLAN:X"]);
    expect(x["QC_PLAN:X"]).toBeLessThan(x["READINESS_APPROVAL:PROJECT"]);
    expect(edges.find((e) => e.target === "QC_PLAN:X")?.animated).toBe(true);
    expect(edges.find((e) => e.target === "READINESS_APPROVAL:PROJECT")?.animated).toBe(false);
  });
});

describe("ReadinessBanner", () => {
  it("shows the server status, revision and hash without inventing readiness", () => {
    const p = { status: "BLOCKED", current_revision_number: 2, baseline_version: 5, baseline_hash: "abcdef0123456789" } as Project;
    render(
      <MemoryRouter>
        <ReadinessBanner project={p} />
      </MemoryRouter>,
    );
    const banner = screen.getByTestId("readiness-banner");
    expect(banner).toHaveTextContent("BLOCKED");
    expect(banner).toHaveTextContent("Baseline R2");
    expect(banner).toHaveTextContent("abcdef0123");
  });
});
