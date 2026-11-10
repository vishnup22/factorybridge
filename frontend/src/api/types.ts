import type { components } from "./schema";

type S = components["schemas"];

export type Project = S["ProjectDetail"];
export type ProjectSummary = S["ProjectSummary"];
export type Blocker = S["BlockerOut"];
export type User = S["UserOut"];
export type Me = S["MeResponse"];
export type LoginResponse = S["LoginResponse"];
export type DocumentDetail = S["DocumentDetail"];
export type DocumentOut = S["DocumentOut"];
export type Extraction = S["ExtractionOut"];
export type Candidate = S["CandidateOut"];
export type CandidateDecision = S["CandidateDecision"];
export type AcceptResponse = S["AcceptResponse"];
export type BomRevision = S["BomRevisionOut"];
export type BomRevisionDetail = S["BomRevisionDetail"];
export type BomItem = S["BomItemOut"];
export type BomDiff = S["BomDiffOut"];
export type LineDiff = S["LineDiffOut"];
export type Change = S["ChangeOut"];
export type ChangeOperation = S["ChangeOperation"];
export type Impact = S["ImpactOut"];
export type ImpactedNode = S["ImpactedNodeOut"];
export type Graph = S["GraphOut"];
export type GraphNode = S["GraphNodeOut"];
export type Artifact = S["ArtifactOut"];
export type ExceptionItem = S["ExceptionOut"];
export type Approval = S["ApprovalOut"];
export type AuditEvent = S["AuditOut"];
export type Supplier = S["SupplierOut"];
export type Rule = S["RuleOut"];
export type Job = S["JobOut"];

export type Role = "customer" | "engineer" | "approver" | "admin";

export interface Envelope<T> {
  data: T;
  meta?: Record<string, unknown>;
}

export interface Problem {
  type?: string;
  title: string;
  status: number;
  detail: string;
  code: string;
  correlation_id?: string | null;
  extra?: Record<string, unknown>;
}
