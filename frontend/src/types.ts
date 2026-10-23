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
export interface Options {
  profiles: Profile[];
  selected_profile_id: number | null;
  products: Product[];
  aspects: Record<string, string>;
  sentiments: Record<string, string>;
  processing_statuses: Record<string, string>;
  human_statuses: Record<string, string>;
}
export interface Health { worker_available: boolean; business_database: string }
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
