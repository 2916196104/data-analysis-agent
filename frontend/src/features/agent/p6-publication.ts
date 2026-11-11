import type { AgentResult, AnalysisScope } from '../../types';

export type P6ChildStatus = 'complete' | 'insufficient_evidence' | 'failed' | 'skipped_budget';
export interface P6Reference {
  review_id: number; product_id: string; published_at: string; source: string; dataset_version: string;
  annotation_id: number; revision_id: number | null; opinion_source: string;
  file_sha256: string; line_number: number; evidence_ref: string; text: string;
  source_result_id: string; aspect: string; topic_id: string; opinion_id: string; sentiment: 'negative';
  quote_start: number; quote_end: number; quote: string;
}
export interface P6Topic {
  topic_id: string; name: string; definition: string; N_a_t: number; sample_denominator: number;
  sample_rate: number; opinion_count: number; semantic_status: 'model_reviewed_not_human_confirmed';
  evidence: P6Reference[];
}
export interface P6Dimension {
  aspect: string; label: string; rank: number; status: P6ChildStatus; reason: string; reason_code: string;
  M_a: number; S_a: number; prepared_reviews: number; negative_rate: number | null; review_coverage: number | null;
  coverage: { selected_reviews: number; selected_opinions: number; classified_opinions: number; evaluated_opinions: number;
    opinion_coverage: number | null; population_opinion_coverage: number | null } | null;
  sampling: Record<string, unknown>; population_mode: string;
  topics_total: number; omitted_topic_count: number; truncation_reason: string | null; topics: P6Topic[];
}
export interface P6Publication {
  version: 'review-p6-publication-v1'; analysis_mode: 'multi_aspect_negative_drilldown';
  status: 'ok' | 'partial' | 'unsupported' | 'error';
  parent_status: 'complete' | 'partial' | 'insufficient_evidence' | 'skipped_budget' | 'failed';
  complete: boolean; scope: AnalysisScope; required_aspects: string[]; publishable_aspects: string[];
  D: number; dimensions: P6Dimension[]; limitations: string[]; ranking_source: Record<string, unknown>;
  selection_basis_sha256: string; publication_sha256: string; answer_text: string;
}

const aspects = ['quality', 'effectiveness', 'price', 'packaging', 'logistics', 'service'];
const count = (n: unknown): n is number => Number.isSafeInteger(n) && Number(n) >= 0;
const rateMatches = (v: unknown, n: number, d: number) => d === 0 ? v === null
  : typeof v === 'number' && Number.isFinite(v) && Math.abs(v - n / d) <= 0.000001;
const text = (v: unknown): v is string => typeof v === 'string' && v.length > 0;

/** 只对服务端 DTO 做结构/口径防御；发布授权与证据语义验收在后端完成。 */
export function readP6Publication(result: AgentResult | undefined): P6Publication | undefined {
  try {
    const p = result?.p6_publication;
    if (!p || p.version !== 'review-p6-publication-v1' || p.analysis_mode !== 'multi_aspect_negative_drilldown'
      || result?.validation?.status !== 'passed' || result.status !== p.status
      || result.task_acceptance?.complete !== p.complete || !count(p.D)
      || !Array.isArray(p.required_aspects) || p.required_aspects.length < 1 || p.required_aspects.length > 3
      || new Set(p.required_aspects).size !== p.required_aspects.length || !p.required_aspects.every(a => aspects.includes(a))
      || !Array.isArray(p.dimensions) || p.dimensions.length !== p.required_aspects.length
      || !Array.isArray(p.publishable_aspects) || !Array.isArray(p.limitations) || p.limitations.length < 4
      || JSON.stringify(result.task_acceptance.required_aspects) !== JSON.stringify(p.required_aspects)
      || !Array.isArray(p.scope.product_ids) || p.scope.product_ids.length !== 1 || !p.scope.product_ids.every(text)
      || ![p.scope.source, p.scope.dataset_version, p.scope.model, p.scope.base_url, p.scope.prompt_version, p.scope.date_from, p.scope.date_to].every(text)
      || Object.keys(result.scope).length !== Object.keys(p.scope).length
      || !p.limitations.every(text) || !text(p.answer_text) || !/^[a-f0-9]{64}$/.test(p.publication_sha256)
      || !/^[a-f0-9]{64}$/.test(p.selection_basis_sha256)
      || Object.entries(p.scope).some(([key, value]) => JSON.stringify(value) !== JSON.stringify((result.scope as unknown as Record<string, unknown>)[key]))) return;
    const published: string[] = [];
    for (const [index, dim] of p.dimensions.entries()) {
      if (dim.aspect !== p.required_aspects[index] || dim.rank !== index + 1 || !text(dim.label)
        || !['complete', 'insufficient_evidence', 'failed', 'skipped_budget'].includes(dim.status)
        || !text(dim.reason) || !text(dim.reason_code) || !count(dim.M_a) || !count(dim.S_a)
        || !count(dim.prepared_reviews) || dim.S_a > dim.prepared_reviews || dim.prepared_reviews > dim.M_a || dim.M_a > p.D
        || !rateMatches(dim.negative_rate, dim.M_a, p.D) || !rateMatches(dim.review_coverage, dim.S_a, dim.M_a)
        || !Array.isArray(dim.topics) || dim.topics.length > 3 || !count(dim.topics_total) || !count(dim.omitted_topic_count)
        || dim.topics_total !== dim.topics.length + dim.omitted_topic_count
        || (dim.omitted_topic_count > 0 ? dim.truncation_reason !== 'per_aspect_top3' : dim.truncation_reason !== null)) return;
      if (dim.status === 'complete') {
        if (!dim.topics.length || !dim.coverage) return;
        published.push(dim.aspect);
      } else if (dim.topics.length || dim.topics_total) return;
      if (dim.coverage && (!count(dim.coverage.selected_opinions) || !count(dim.coverage.classified_opinions)
        || !count(dim.coverage.evaluated_opinions) || dim.coverage.selected_reviews !== dim.S_a
        || dim.coverage.selected_opinions < dim.S_a || dim.coverage.evaluated_opinions > dim.coverage.selected_opinions
        || dim.coverage.classified_opinions > dim.coverage.evaluated_opinions
        || !rateMatches(dim.coverage.opinion_coverage, dim.coverage.classified_opinions, dim.coverage.selected_opinions))) return;
      if (new Set(dim.topics.map(t => t.topic_id)).size !== dim.topics.length) return;
      for (const topic of dim.topics) {
        if (!text(topic.name) || !text(topic.definition) || !text(topic.topic_id) || !count(topic.N_a_t) || topic.N_a_t < 1
          || topic.N_a_t > dim.S_a || topic.sample_denominator !== dim.S_a || !count(topic.opinion_count)
          || topic.opinion_count < topic.N_a_t || !rateMatches(topic.sample_rate, topic.N_a_t, dim.S_a)
          || topic.semantic_status !== 'model_reviewed_not_human_confirmed' || !Array.isArray(topic.evidence) || !topic.evidence.length) return;
        for (const ref of topic.evidence) {
          if (ref.aspect !== dim.aspect || ref.topic_id !== topic.topic_id || ref.source_result_id !== `r${index + 1}`
            || ref.sentiment !== 'negative' || !count(ref.review_id) || !count(ref.annotation_id) || !text(ref.opinion_id)
            || !count(ref.line_number) || ref.line_number < 1 || !/^[a-f0-9]{64}$/.test(ref.file_sha256)
            || ref.evidence_ref !== `sha256:${ref.file_sha256}#line=${ref.line_number}`
            || !p.scope.product_ids.includes(ref.product_id) || ref.source !== p.scope.source || ref.dataset_version !== p.scope.dataset_version
            || !text(ref.published_at) || !text(ref.text) || !text(ref.quote) || !text(ref.opinion_source)
            || !count(ref.quote_start) || !count(ref.quote_end) || ref.quote_start >= ref.quote_end
            || ref.quote_end > Array.from(ref.text).length
            || Array.from(ref.text).slice(ref.quote_start, ref.quote_end).join('') !== ref.quote) return;
        }
      }
    }
    if (JSON.stringify(published) !== JSON.stringify(p.publishable_aspects)) return;
    const expected = published.length === p.required_aspects.length ? 'complete' : published.length ? 'partial'
      : p.dimensions.every(d => d.status === 'insufficient_evidence') ? 'insufficient_evidence'
      : p.dimensions.every(d => d.status === 'skipped_budget') ? 'skipped_budget' : 'failed';
    const status = { complete: 'ok', partial: 'partial', insufficient_evidence: 'unsupported', skipped_budget: 'error', failed: 'error' }[expected];
    if (p.parent_status !== expected || p.status !== status || p.complete !== (expected === 'complete')) return;
    return p;
  } catch { return; }
}

export const p6StateLabel: Record<P6Publication['parent_status'], string> = {
  complete: '分析完成', partial: '部分完成', insufficient_evidence: '证据不足', skipped_budget: '预算不足，未执行', failed: '分析失败',
};
