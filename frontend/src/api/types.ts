export interface ProjectRow {
  project_id: string;
  name: string;
  created_ts: number;
}

export interface ProfileInfo {
  active: string;
  available: string[];
}

export interface ProfileSwitchResponse {
  active: string;
}

export interface TunableRow {
  key: string;
  value: number;
  default: number;
}

export interface ModelCallRow {
  id?: number;
  ts: number;
  role: string;
  provider: string;
  model: string;
  input_tokens: number | null;
  output_tokens: number | null;
  latency_ms: number | null;
  cost_usd: number | null;
  outcome: string;
  error: string | null;
}

export interface SpendSummary {
  recent_calls: ModelCallRow[];
  total_cost_usd: number;
  total_calls: number;
  by_role: Record<string, number>;
}

export interface SourceRow {
  source_id: string;
  ref: string;
  role: string;
  status: string;
  total_chunks: number;
  done_chunks: number;
  failed_chunks: number;
  rejected_chunks: number;
  error: string | null;
  ts: number;
}

export interface IngestResponse {
  source_id: string;
}

export interface DomainProfileResponse {
  profile: string;
}

export interface SynthesizeResponse {
  enqueued: boolean;
  already_running: boolean;
  worker_online: boolean;
}

export interface FailureGroup {
  error: string;
  count: number;
  sample_chunk_ids: string[];
}

export interface RetryFailedResponse {
  source_id: string;
  status: string;
  retried_chunks: number;
}

export interface HealthResponse {
  api: boolean;
  worker: boolean;
  redis: boolean;
  neo4j: boolean;
}

export interface SynthesisProgress {
  phase: "grouping" | "analyzing" | "ordering";
  done: number;
  total: number;
  ts: number;
}

export interface SynthesisStatusResponse {
  last_run: {
    ts: number;
    dirty_concepts: number;
    processed_concepts: number;
    curriculum_len: number;
  } | null;
  last_error?: { ts: number; error: string } | null;
  running_since?: number | null;
  progress?: SynthesisProgress | null;
}

export interface SpendTotals {
  total_cost_usd: number;
  total_calls: number;
}

export interface StatusResponse {
  worker: boolean;
  redis: boolean;
  neo4j: boolean;
  spend: SpendTotals;
  synthesis: SynthesisStatusResponse;
  /** Only present when background jobs are stuck in an unconsumed queue. */
  dead_letter_count?: number;
}

export interface ConceptMeta {
  concept_id: string;
  name: string;
  summary: string;
  order_index: number | null;
  conflict_count?: number;
  dirty?: boolean;
  teach_md?: string;
  teach_at?: number | null;
}

export interface ClaimRow {
  claim_id: string;
  text: string;
  stance: string;
  source_id: string;
  trust?: string;
  quote?: string;
}

export interface ConflictRow {
  claim_a: string;
  claim_b: string;
  classification: string;
  rationale: string;
}

export interface CitationRow {
  claim_id: string;
  source_id: string;
  kind?: string | null;
  seq?: number | null;
  page?: number | null;
  para_index?: number | null;
  href?: string | null;
  url?: string | null;
  start_s?: number | null;
  end_s?: number | null;
}

export interface ConceptDetail {
  concept: ConceptMeta;
  claims: ClaimRow[];
  conflicts: ConflictRow[];
  citations: CitationRow[];
}

export interface TeachResponse {
  markdown: string;
  cached?: boolean;
}

export interface QuizNext {
  concept_id: string;
  question: string;
}

export interface QuizGrade {
  correct: boolean;
  score_0_100: number;
  explanation: string;
}

export interface QuizStatRow {
  concept_id: string;
  attempts: number;
  correct: number;
  incorrect: number;
  avg_score: number;
  last_score: number | null;
  last_correct: boolean | null;
}

export interface MemoryItem {
  memory_id: string;
  text: string;
  category: string;
  created_at: number;
}

export interface MemoryListResponse {
  items: MemoryItem[];
}

export interface ExportResponse {
  root: string;
  files: Record<string, string[]>;
}

export interface ChatDeltaFrame {
  delta: string;
}

export interface ChatDoneFrame {
  done: true;
  citations: string[];
}

export interface ChatErrorFrame {
  error: string;
}

export type ChatFrame = ChatDeltaFrame | ChatDoneFrame | ChatErrorFrame;

export interface ChatSessionTurn {
  question: string;
  answer: string;
}

export interface ChatSessionResponse {
  turns: ChatSessionTurn[];
}

export interface FlagClaimResponse {
  claim_id: string;
  concept_id: string;
  status: string;
}

export interface EvalRun {
  id: number;
  ts: number;
  kind: string;
  git_sha: string | null;
  passed: number;
}

export interface EvalMetric {
  metric: string;
  value: number;
  gate: number | null;
  passed: number;
}

export interface EvalReport {
  run: EvalRun | null;
  metrics: EvalMetric[];
}
