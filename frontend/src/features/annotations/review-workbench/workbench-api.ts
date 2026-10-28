import { errorMessage, request } from '../../../api';

export interface WorkbenchProfile {
  profile_id: number;
  requested_model: string;
  base_url: string;
  prompt_version: string;
  newest_id: number;
}

export interface WorkbenchProduct {
  key: string;
  source: string;
  dataset_version: string;
  product_id: string;
  product_title: string | null;
  review_count: number;
  eligible_count: number;
  empty_count: number;
  pending_count: number;
  success_count: number;
  failed_count: number;
  reviewed_count: number;
}

export interface WorkbenchOptions {
  profiles: WorkbenchProfile[];
  selected_profile_id: number | null;
  products: WorkbenchProduct[];
  aspects: Record<string, string>;
  sentiments: Record<string, string>;
  processing_statuses: Record<string, string>;
  human_statuses: Record<string, string>;
}

export interface WorkbenchOpinion {
  aspect: string;
  sentiment: string;
  quote: string;
  quote_start: number;
  quote_end: number;
}

export interface WorkbenchReview {
  id: number;
  source: string;
  dataset_version: string;
  product_id: string;
  product_title: string | null;
  title: string;
  text: string;
  rating: number | null;
  published_at: string;
  processing_status: string;
  human_status: string;
  error_code?: string | null;
  annotation_id: number | null;
  revision_id: number | null;
  opinions: WorkbenchOpinion[];
}

export interface WorkbenchPage {
  total: number;
  page: number;
  page_size: number;
  pages: number;
  items: WorkbenchReview[];
}

export interface WorkbenchReviewDetail {
  review: WorkbenchReview & {
    content_sha256: string;
    file_path: string;
    file_sha256: string;
    line_number: number;
    evidence_ref: string;
  };
  annotation: {
    id: number;
    status: string;
    requested_model: string;
    base_url: string;
    prompt_version: string;
    opinions: WorkbenchOpinion[];
  } | null;
  effective_opinions: WorkbenchOpinion[];
  human_reviews: Array<{
    id: number;
    action: 'confirmed' | 'corrected';
    reviewer: string;
    note: string;
    created_at: string;
    opinions: WorkbenchOpinion[];
  }>;
  model_history: Array<{
    id: number;
    status: string;
    created_at?: string;
    error_code?: string | null;
    opinions: WorkbenchOpinion[];
  }>;
}

export interface HumanOpinionDraft {
  aspect: string;
  sentiment: string;
  quote: string;
}

export interface HumanReviewInput {
  annotation_id: number;
  expected_revision_id: number | null;
  action: 'confirmed' | 'corrected';
  reviewer: string;
  note?: string;
  opinions?: HumanOpinionDraft[];
}

export interface HumanReviewResult {
  revision_id: number;
  action: 'confirmed' | 'corrected';
  opinion_count: number;
}

function params(values: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams();
  Object.entries(values).forEach(([key, value]) => {
    if (value !== undefined && value !== '') search.set(key, String(value));
  });
  const query = search.toString();
  return query ? `?${query}` : '';
}

export const workbenchApi = {
  options: (profileId?: number) => request<WorkbenchOptions>(`/options${params({ profile_id: profileId })}`),
  reviews: (filters: Record<string, string | number | undefined>, signal?: AbortSignal) =>
    request<WorkbenchPage>(`/reviews${params(filters)}`, { signal }),
  review: (id: number, profileId?: number, signal?: AbortSignal) =>
    request<WorkbenchReviewDetail>(`/reviews/${encodeURIComponent(id)}${params({ profile_id: profileId })}`, { signal }),
  saveHumanReview: (id: number, input: HumanReviewInput) => request<HumanReviewResult>(
    `/reviews/${encodeURIComponent(id)}/human-reviews`, { method: 'POST', body: JSON.stringify(input) },
  ),
};

export { errorMessage };
