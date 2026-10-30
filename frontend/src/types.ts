export type TaskStatus = 'queued' | 'running' | 'succeeded' | 'partial' | 'failed';
export type TaskKind = 'analysis' | 'import' | 'label' | 'replay';
export interface Page<T> { total: number; page: number; page_size: number; items: T[] }
export interface ApiErrorBody {
  code: string;
  message: string;
  details?: { location: (string | number)[]; message: string; type: string }[];
}
export interface Progress {
  stage: string;
  completed: number;
  total: number | null;
  unit: string;
  [key: string]: unknown;
}
export interface TaskSummary {
  task_id: string;
  kind: TaskKind;
  status: TaskStatus;
  parent_task_id: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  updated_at: string;
  progress: Progress;
  error: ApiErrorBody | null;
}
export interface Identity { model: string; base_url: string; prompt_version: string }
export interface AnalysisScope extends Identity {
  product_ids: string[];
  source?: string | null;
  dataset_version?: string | null;
  date_from: string;
  date_to: string;
  as_of?: string | null;
}
export interface AnalysisParameters extends AnalysisScope {
  baseline_from: string;
  baseline_to: string;
  question?: string;
}
export interface AnnotationDefaults {
  dataset: string;
  products: string[];
  limit?: number;
  workers?: number;
  scope?: Pick<AnalysisScope, 'date_from' | 'date_to' | 'as_of'>;
  retryFailed?: boolean;
}
export interface ImportParameters {
  reviews_path?: string;
  meta_path?: string;
  dataset_version: string;
  product_ids: string[];
  max_reviews?: number | null;
}
export interface UploadedImportFile {
  kind: 'reviews' | 'meta';
  filename: string;
  size_bytes: number;
  path: string;
}
export interface LabelParameters {
  source: string;
  dataset_version: string;
  product_ids: string[];
  limit: number;
  workers: number;
  retry_failed: boolean;
  date_from?: string;
  date_to?: string;
  as_of?: string | null;
}
export type TaskInput = { kind: 'analysis'; parameters: AnalysisParameters }
  | { kind: 'import'; parameters: ImportParameters }
  | { kind: 'label'; parameters: LabelParameters };
export interface Task extends TaskSummary {
  parameters: Record<string, unknown>;
  result: ({ run_id?: string; [key: string]: unknown }) | null;
}
export interface Submission { task: Task; reused: boolean }
export interface Product {
  key: string;
  source: string;
  dataset_version: string;
  product_id: string;
  product_title: string | null;
  review_count: number;
  success_count: number;
  pending_count: number;
}
export interface Profile {
  profile_id: number;
  requested_model: string;
  base_url: string;
  prompt_version: string;
}
export interface DatasetSummary {
  source: string;
  dataset_version: string;
  review_count: number;
  product_count: number;
  eligible_text_reviews: number;
  published_at_start: string | null;
  published_at_end: string | null;
  profiles: { profile_id: number; successful_reviews: number; labeling_coverage: number | null }[];
}
export interface Options {
  datasets?: DatasetSummary[];
  profiles: Profile[];
  selected_profile_id: number | null;
  products: Product[];
  aspects: Record<string, string>;
  sentiments: Record<string, string>;
  processing_statuses: Record<string, string>;
  human_statuses: Record<string, string>;
}
export interface Health {
  worker_available: boolean;
  business_database: string;
  redis_worker_required?: boolean;
  redis_worker_available?: boolean | null;
  redis_queue?: { backlog_reviews: number | null; processing_reviews: number | null };
}
export interface Quality {
  review_count: number;
  product_count: number;
  published_at_start: string | null;
  published_at_end: string | null;
}
export interface Opinion {
  aspect: string;
  sentiment: string;
  quote: string;
  quote_start: number;
  quote_end: number;
}
export interface Review {
  id: number;
  product_id: string;
  product_title: string | null;
  title: string;
  text: string;
  rating: number | null;
  published_at: string;
  processing_status: string;
  human_status: string;
  opinions: Opinion[];
}
export interface Summary {
  review_count: number;
  eligible_text_reviews: number;
  empty_text_reviews?: number;
  reviewed_reviews?: number;
  successful_reviews: number;
  failed_reviews: number;
  unprocessed_reviews: number;
  negative_review_count: number;
  labeling_coverage: number | null;
  negative_rate: number | null;
}
export interface Issue {
  rank: number;
  aspect: string;
  aspect_label: string;
  negative_review_count: number;
  negative_rate: number | null;
  baseline_negative_rate: number | null;
  negative_rate_change_pp: number | null;
}
export interface AgentEvidence {
  review_id: number;
  product_id: string;
  text: string;
  published_at: string;
  evidence_ref: string;
  opinion_source: string;
  opinions: Opinion[];
}
export interface AspectSummary extends Summary {
  aspects: Record<string, { negative_review_count: number; negative_rate: number | null }>;
}
export interface RatingGroup {
  source: string; dataset_version: string; group: string; review_count: number; rated_reviews: number;
  missing_rating_reviews: number; other_rating_reviews: number;
  stars: { star: number; review_count: number; rate: number | null }[];
}
export interface PositiveAspect {
  rank: number; aspect: string; aspect_label: string; positive_review_count: number;
  positive_rate: number | null; positive_success_rate: number | null;
  positive_rate_denominator: number; positive_success_rate_denominator: number;
}
export type AgentToolResult = { result_id: string; parameters: Record<string, unknown> } & (
  { kind: 'context'; data: { summary: Summary; products?: { product_id: string; title: string | null }[] } }
  | { kind: 'trend'; data: { period: string; summary: Summary; buckets: (Summary & { bucket: string })[] } }
  | { kind: 'comparison'; data: { products: (Summary & { product_id: string })[]; no_reviews_product_ids?: string[] } }
  | { kind: 'issues'; data: { issues: Issue[]; current?: Summary; baseline?: Summary } }
  | { kind: 'evidence'; data: { total: number; page: number; items: AgentEvidence[] } }
  | { kind: 'quality'; data: { scope: AnalysisScope; summary: Summary; missing_text_rate: number | null; failed_rate: number | null;
      failures: { error_code: string | null; review_count: number }[]; sources: { file_sha256: string; review_count: number }[] } }
  | { kind: 'aspect_trend'; data: { scope: AnalysisScope; period: string; aspects: Record<string, string>;
      summary: AspectSummary; buckets: (AspectSummary & { bucket: string })[] } }
  | { kind: 'ratings'; data: { scope: AnalysisScope; group_by: 'product' | 'month' | 'all'; groups: RatingGroup[] } }
  | { kind: 'positive_aspects'; data: { analysis_type: 'positive_aspects'; sentiment: 'positive';
      data_status: 'ok' | 'no_positive_mentions' | 'insufficient_data'; scope: AnalysisScope;
      summary: Summary & { positive_review_count: number }; aspects: PositiveAspect[] } }
  | { kind: 'window_comparison'; data: { scope: AnalysisScope; baseline_scope: AnalysisScope;
      current: Summary; baseline: Summary; current_days: number; baseline_days: number;
      changes: Record<string, number | null>; issues: Issue[] } }
);
export interface AgentResult {
  agent: true;
  status: 'ok' | 'unsupported' | 'error';
  question: string;
  answer?: string;
  answer_sections?: { result_id: string; kind: AgentToolResult['kind']; text: string }[];
  scope: AnalysisScope & Partial<Pick<AnalysisParameters, 'baseline_from' | 'baseline_to'>>;
  snapshot_at: string;
  agent_model_identity?: { model: string; base_url: string; prompt_version: string };
  result_ids?: string[];
  results: Record<string, AgentToolResult>;
  warnings?: string[];
  validation?: { status: 'passed' | 'failed'; checks: { name: string; passed: boolean; detail: string }[] };
  model_calls: number;
  tool_calls: number;
  model_tool_attempts?: number;
  cache_hits?: number;
  query_executions?: number;
  elapsed_seconds: number;
  trace: { event: string; tool?: string; status?: string; elapsed_ms?: number; result_id?: string }[];
}
export interface Run {
  run_id: string;
  created_at: string;
  as_of: string;
  request: { scope: AnalysisScope; baseline_scope: AnalysisScope; rules: Record<string, unknown> };
  inputs: { review_id: number; product_id: string; text: string; published_at: string; opinions: Opinion[]; evidence_ref: string }[];
  results: {
    summary: { input_reviews: number; products: number; triggered: number; normal: number; insufficient_data: number };
    trend: { summary: Summary; buckets: (Summary & { bucket: string })[] };
    comparison: { products: (Summary & { product_id: string; source: string; dataset_version: string })[] };
    products: { product: { product_id: string }; top_issues: { issues: Issue[] }; evaluations: { aspect: string; status: string }[] }[];
  };
}
