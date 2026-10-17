// Shapes returned by the SupplyGraph API (backend/supplygraph).

export type EntityKind =
  | "Supplier"
  | "Part"
  | "Product"
  | "Customer"
  | "Region"
  | "Port"
  | "Disruption";

export type Outcome = "answered" | "from_memory" | "not_in_graph" | "unknown_entity" | "ambiguous";

export interface EntityRef {
  id: string;
  name: string;
  kind: EntityKind | string;
}

export interface ToolResult<D = Record<string, unknown>> {
  tool: string;
  outcome: Outcome;
  summary: string;
  data: D;
  entities: EntityRef[];
  paths: EntityRef[][];
  candidates: EntityRef[];
  query_ms: number;
  rows: number;
}

export interface DatasetColumn {
  name: string;
  type: string;
  required: boolean;
  description: string;
  choices: string[];
  minimum: number | null;
  maximum: number | null;
  example: string;
}

export interface Dataset {
  name: string;
  title: string;
  description: string;
  required: boolean;
  columns: DatasetColumn[];
}

export interface ToolSpec {
  name: string;
  description: string;
  read_only: boolean;
  input_schema: Record<string, unknown>;
}

export interface Meta {
  version: string;
  workspace: string;
  workspace_exists: boolean;
  currency: string;
  llm_configured: boolean;
  llm_provider: string;
  model: string;
  entity_kinds: EntityKind[];
  limits: { import_max_bytes: number; pdf_max_bytes: number; network_max_nodes: number };
  datasets: Dataset[];
  tools: ToolSpec[];
}

export interface Stats {
  graph: string;
  nodes: Record<string, number>;
  relationships: Record<string, number>;
  findings: number;
  documents: number;
}

export interface DisruptionCard {
  id: string;
  name: string;
  category: string;
  severity: number;
  started_at: string;
  expected_end: string | null;
  source: string | null;
  affects: EntityRef[];
  outcome: Outcome;
  summary: string;
  suppliers_hit: number;
  suppliers_degraded: number;
  outage_days: number | null;
  revenue_at_risk: number;
  revenue_at_risk_display: string;
  line_stop_products: string[];
  constrained_products: string[];
  stockouts: string[];
}

export interface SinglePoint {
  id: string;
  name: string;
  risk: "single_source" | "single_country";
  suppliers: string[];
  countries: string[];
  inventory_days: number;
  products: string[];
  revenue_exposed: number;
  revenue_exposed_display: string;
}

export interface CriticalSupplier {
  id: string;
  name: string;
  sole_source_revenue: number;
  sole_source_revenue_display: string;
  revenue_reach: number;
  revenue_reach_display: string;
  sole_source_parts: string[];
  products: string[];
  status: "available" | "degraded" | "disrupted";
}

export interface Overview {
  currency: string;
  stats: Stats;
  kpis: {
    active_disruptions: number;
    revenue_at_risk: number;
    revenue_at_risk_display: string;
    revenue_total: number;
    revenue_total_display: string;
    products_at_risk: number;
    single_source_parts: number;
    single_country_parts: number;
    suppliers_disrupted: number;
  };
  disruptions: DisruptionCard[];
  /** Products that stop only because active disruptions combine. */
  compound: {
    products: { id: string; name: string; critical_path: string | null }[];
    revenue_at_risk: number;
    revenue_at_risk_display: string;
    blocked_parts: string[];
  };
  single_points: SinglePoint[];
  critical_suppliers: CriticalSupplier[];
  suggestions: string[];
}

export type NodeStatus = "normal" | "disrupted" | "degraded" | "affected" | "disruption";

export interface GraphNode {
  id: string;
  name: string;
  kind: EntityKind;
  degree: number;
  status: NodeStatus;
}

export interface GraphLink {
  source: string;
  target: string;
  type: string;
}

export interface GraphPayload {
  nodes: GraphNode[];
  links: GraphLink[];
  truncated: boolean;
  focus: string | null;
}

export interface EntityRelationship {
  type: string;
  outgoing: boolean;
  id: string;
  name: string;
  kind: EntityKind;
  props: Record<string, unknown>;
}

export interface EntityDetail {
  id: string;
  name: string;
  kind: EntityKind;
  properties: Record<string, unknown>;
  relationships: EntityRelationship[];
  status?: string;
  documents?: { id: string; filename: string; issuer: string | null; uploaded_at: string }[];
  passages?: { id: string; text: string; page: number; filename: string; hits?: number }[];
}

export interface ImpactProduct {
  id: string;
  name: string;
  annual_revenue: number;
  status: "line_stop_risk" | "constrained";
  days_of_cover: number | null;
  stockout_expected?: boolean;
  critical_path: string | null;
  affected_parts: {
    part: string;
    share_lost: number;
    blocked: boolean;
    buffer_days: number;
    hops: number;
  }[];
}

export interface ImpactData {
  start: EntityRef;
  outage_days: number | null;
  currency: string;
  suppliers_hit: { id: string; name: string; region: string; reason: string }[];
  suppliers_degraded: { id: string; name: string; blocked_ports: string[]; open_ports: string[] }[];
  parts: {
    id: string;
    name: string;
    share_lost: number;
    inventory_days: number;
    hit_suppliers: string[];
    unaffected_alternates: { id: string; name: string; share: number; lead_time_days: number }[];
  }[];
  products: ImpactProduct[];
  customers: { id: string; name: string; revenue_at_risk: number; revenue_exposed: number }[];
  revenue_at_risk: number;
  revenue_at_risk_display: string;
  revenue_exposed: number;
  revenue_exposed_display: string;
  notes: string;
}

export interface ToolTrace {
  tool: string;
  arguments: Record<string, unknown>;
  outcome: Outcome | null;
  rows: number;
  elapsed_ms: number;
  summary: string | null;
  error: string | null;
}

export interface AgentAnswer {
  answer: string;
  grounded: boolean;
  violations: string[];
  session_id: string;
  tenant: string;
  tool_calls: ToolTrace[];
  paths: EntityRef[][];
  entities: EntityRef[];
  stop_reason: string | null;
  provider: string;
  model: string;
  usage: Record<string, number>;
  elapsed_ms: number;
}

export interface ImportIssue {
  severity: "error" | "warning";
  message: string;
  file: string | null;
  row: number | null;
  column: string | null;
}

export interface ImportReport {
  ok: boolean;
  files: Record<string, string>;
  rows: Record<string, number>;
  issues: ImportIssue[];
  dry_run: boolean;
  mode: "merge" | "replace";
  workspace: string;
  written?: Record<string, number>;
}

export interface ProposedTarget {
  name: string;
  kind_hint: string;
  evidence: string;
  match: EntityRef | null;
  candidates: EntityRef[];
  status: "matched" | "ambiguous" | "unmatched";
}

export interface ProposedEvent {
  proposed_id: string;
  title: string;
  category: string;
  severity: number;
  start_date: string | null;
  expected_end_date: string | null;
  summary: string;
  targets: ProposedTarget[];
}

export interface DocumentMeta {
  sha256: string;
  filename: string;
  size_bytes: number;
  issuer: string | null;
  already_ingested: boolean;
}

export interface Proposal {
  document: DocumentMeta;
  is_supply_risk_document: boolean;
  events: ProposedEvent[];
}

export interface IngestedDocument {
  id: string;
  filename: string;
  issuer: string | null;
  uploaded_at: string;
  sha256: string;
  passages: number;
  disruptions: { id: string; name: string; active: boolean }[];
}

export interface Finding {
  id: string;
  text: string;
  created_at: string;
  session_id: string;
  author: string;
  entity: EntityRef;
}

export interface SampleDocument {
  file: string;
  title: string;
  issuer: string;
}

export interface Passage {
  id: string;
  text: string;
  page: number;
  /** Hybrid relevance: 70% meaning (vector) + 30% keyword overlap. */
  score: number;
  similarity: number;
  keyword_match: number;
  document: { id: string; filename: string; issuer: string | null; uploaded_at: string };
  disruptions: (EntityRef & { active: boolean })[];
  mentions: EntityRef[];
}

export interface CommitResult {
  document_id: string;
  disruptions: { id: string; affects: number }[];
  passages: { indexed: boolean; passages: number; linked?: number; pages?: number; warning?: string };
}
