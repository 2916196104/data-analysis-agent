import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Alert, Button, Card, Empty, Input, Pagination, Select, Skeleton, Space, Table, Tag } from 'antd';
import { CheckOutlined, ReloadOutlined, SafetyCertificateOutlined } from '@ant-design/icons';
import {
  errorMessage, workbenchApi, type HumanOpinionDraft, type WorkbenchOptions,
  type WorkbenchOpinion, type WorkbenchPage, type WorkbenchProduct, type WorkbenchReviewDetail,
} from './workbench-api';
import './review-workbench.css';

interface ReviewFilters {
  product_key?: string;
  date_from?: string;
  date_to?: string;
  aspect?: string;
  sentiment?: string;
  processing_status?: string;
  human_status?: string;
}

const emptyFilters: ReviewFilters = {};
const legacyUrl = import.meta.env.VITE_REVIEW_WORKBENCH_URL || 'http://127.0.0.1:8765';

function mappingOptions(mapping: Record<string, string>, all = '全部') {
  return [{ value: '', label: all }, ...Object.entries(mapping).map(([value, label]) => ({ value, label }))];
}

function formatDate(value: string): string {
  return value ? `${value.slice(0, 10)} UTC` : '日期缺失';
}

function reviewStatusLabel(options: WorkbenchOptions | undefined, value: string): string {
  return options?.processing_statuses[value] || options?.human_statuses[value] || value;
}

function HighlightedText({ text, opinion }: { text: string; opinion?: WorkbenchOpinion }) {
  if (!opinion || opinion.quote_start < 0 || opinion.quote_end <= opinion.quote_start) {
    return <>{text || '（空正文）'}</>;
  }
  return <>
    {text.slice(0, opinion.quote_start)}
    <mark>{text.slice(opinion.quote_start, opinion.quote_end)}</mark>
    {text.slice(opinion.quote_end)}
  </>;
}

function OpinionList({ opinions, options, onSelect }: {
  opinions: WorkbenchOpinion[]; options?: WorkbenchOptions; onSelect?: (opinion: WorkbenchOpinion) => void;
}) {
  if (!opinions.length) return <p className="muted">无观点</p>;
  return <div className="review-opinion-list">
    {opinions.map((opinion, index) => <div className="review-opinion" key={`${opinion.aspect}-${opinion.sentiment}-${index}`}>
      <Tag color="blue">{options?.aspects[opinion.aspect] || opinion.aspect}</Tag>
      <Tag color={opinion.sentiment === 'negative' ? 'red' : opinion.sentiment === 'positive' ? 'green' : 'default'}>
        {options?.sentiments[opinion.sentiment] || opinion.sentiment}
      </Tag>
      <span className="review-quote">{opinion.quote}</span>
      {onSelect && <Button type="link" size="small" onClick={() => onSelect(opinion)}>定位原文</Button>}
    </div>)}
  </div>;
}

function productProgress(products: WorkbenchProduct[]) {
  return products.map(product => ({
    ...product,
    progress: product.eligible_count ? `${((product.success_count / product.eligible_count) * 100).toFixed(1)}%` : '无有效正文',
  }));
}

export default function ReviewWorkbenchPage({ onRefresh }: { onRefresh?: () => void }) {
  const [options, setOptions] = useState<WorkbenchOptions>();
  const [profileId, setProfileId] = useState<number>();
  const [filters, setFilters] = useState<ReviewFilters>(emptyFilters);
  const [draftFilters, setDraftFilters] = useState<ReviewFilters>(emptyFilters);
  const [pageSize, setPageSize] = useState(20);
  const [reviews, setReviews] = useState<WorkbenchPage>();
  const [selectedId, setSelectedId] = useState<number>();
  const [detail, setDetail] = useState<WorkbenchReviewDetail>();
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [reviewer, setReviewer] = useState('');
  const [note, setNote] = useState('');
  const [opinions, setOpinions] = useState<HumanOpinionDraft[]>([]);
  const [highlighted, setHighlighted] = useState<WorkbenchOpinion>();
  const textRef = useRef<HTMLDivElement>(null);
  const requestRevision = useRef(0);
  const detailRevision = useRef(0);

  const loadOptions = useCallback(async (nextProfile?: number) => {
    try {
      const next = await workbenchApi.options(nextProfile);
      setOptions(next);
      setProfileId(next.selected_profile_id ?? nextProfile);
      setError('');
    } catch (requestError) {
      setError(errorMessage(requestError));
    }
  }, []);

  const loadReviews = useCallback(async (nextPage = 1) => {
    if (!options) return;
    const revision = ++requestRevision.current;
    setLoading(true);
    try {
      const result = await workbenchApi.reviews({
        profile_id: profileId,
        ...filters,
        page: nextPage,
        page_size: pageSize,
      });
      if (revision !== requestRevision.current) return;
      setReviews(result);
      setSelectedId(undefined);
      setDetail(undefined);
      setError('');
    } catch (requestError) {
      if (revision === requestRevision.current) setError(errorMessage(requestError));
    } finally {
      if (revision === requestRevision.current) setLoading(false);
    }
  }, [filters, options, pageSize, profileId]);

  const openDetail = useCallback(async (id: number) => {
    const revision = ++detailRevision.current;
    setSelectedId(id);
    setDetailLoading(true);
    setHighlighted(undefined);
    try {
      const next = await workbenchApi.review(id, profileId);
      if (revision !== detailRevision.current) return;
      setDetail(next);
      setReviewer('');
      setNote('');
      setOpinions(next.effective_opinions.map(opinion => ({
        aspect: opinion.aspect, sentiment: opinion.sentiment, quote: opinion.quote,
      })));
      setError('');
    } catch (requestError) {
      if (revision === detailRevision.current) setError(errorMessage(requestError));
    } finally {
      if (revision === detailRevision.current) setDetailLoading(false);
    }
  }, [profileId]);

  useEffect(() => { void loadOptions(); }, [loadOptions]);
  useEffect(() => { void loadReviews(1); }, [loadReviews]);

  const products = useMemo(() => productProgress(options?.products || []), [options?.products]);
  const selectedReview = detail?.review;
  const canEdit = !!selectedReview && selectedReview.processing_status === 'success' && !!detail?.annotation;
  const productOptions = [{ value: '', label: '全部商品' }, ...(options?.products || []).map(product => ({
    value: product.key,
    label: `${product.product_id} · ${product.product_title || '商品名称缺失'}`,
  }))];

  function updateFilter(key: keyof ReviewFilters, value: string) {
    setDraftFilters(current => ({ ...current, [key]: value || undefined }));
  }

  function applyFilters() {
    setFilters({ ...draftFilters });
  }

  function resetFilters() {
    setDraftFilters(emptyFilters);
    setFilters(emptyFilters);
  }

  function addOpinion(quote = '') {
    setOpinions(current => [...current, {
      aspect: Object.keys(options?.aspects || {})[0] || 'quality',
      sentiment: Object.keys(options?.sentiments || {})[0] || 'negative',
      quote,
    }]);
  }

  function updateOpinion(index: number, key: keyof HumanOpinionDraft, value: string) {
    setOpinions(current => current.map((opinion, currentIndex) => currentIndex === index ? { ...opinion, [key]: value } : opinion));
  }

  function selectText() {
    const selection = window.getSelection();
    const quote = selection?.toString() || '';
    if (!quote || !textRef.current || !selection?.anchorNode || !selection.focusNode
      || !textRef.current.contains(selection.anchorNode) || !textRef.current.contains(selection.focusNode)) {
      setError('请先在评论原文中选中连续文字。');
      return;
    }
    addOpinion(quote);
    setError('');
  }

  async function save(action: 'confirmed' | 'corrected') {
    if (!detail || !selectedReview || !detail.annotation) return;
    if (!reviewer.trim()) { setError('请填写复核人。'); return; }
    setSaving(true);
    setError('');
    try {
      await workbenchApi.saveHumanReview(selectedReview.id, {
        annotation_id: detail.annotation.id,
        expected_revision_id: selectedReview.revision_id,
        action,
        reviewer: reviewer.trim(),
        note,
        ...(action === 'corrected' ? { opinions } : {}),
      });
      setNotice('复核已保存，模型原始记录仍然保留。');
      await loadOptions(profileId);
      await loadReviews(reviews?.page || 1);
      await openDetail(selectedReview.id);
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally { setSaving(false); }
  }

  return <section className="review-workbench-page" aria-labelledby="review-workbench-title">
    <div className="review-page-heading">
      <div><h1 id="review-workbench-title">人工复核</h1><p>检查模型观点、定位原文并保存人工确认或纠正结果。</p></div>
      <Space wrap>
        <Button icon={<ReloadOutlined />} loading={loading} onClick={() => { void loadOptions(profileId); void loadReviews(reviews?.page || 1); onRefresh?.(); }}>刷新</Button>
        <Button href={legacyUrl} target="_blank" rel="noreferrer">打开原生工作台</Button>
      </Space>
    </div>
    {error && <Alert className="spaced-bottom" type="error" showIcon message="人工复核请求失败" description={error} closable onClose={() => setError('')} />}
    {notice && <Alert className="spaced-bottom" type="success" showIcon message={notice} closable onClose={() => setNotice('')} />}
    {!options && !error && <Skeleton active paragraph={{ rows: 8 }} />}
    {options && <>
      <Card className="review-workbench-progress" title="商品标注进度">
        <div className="review-workbench-metrics">
          <span><strong>{products.reduce((sum, item) => sum + item.review_count, 0)}</strong>评论总数</span>
          <span><strong>{products.reduce((sum, item) => sum + item.success_count, 0)}</strong>标注成功</span>
          <span><strong>{products.reduce((sum, item) => sum + item.pending_count, 0)}</strong>待标注</span>
          <span><strong>{products.reduce((sum, item) => sum + item.reviewed_count, 0)}</strong>人工复核</span>
        </div>
        <Table size="small" rowKey="key" pagination={false} dataSource={products} scroll={{ x: 720 }} columns={[
          { title: '商品', dataIndex: 'product_id', render: (value: string, product: WorkbenchProduct) => <><strong>{value}</strong><div className="muted">{product.product_title || '商品名称缺失'}</div></> },
          { title: '评论数', dataIndex: 'review_count' }, { title: '成功 / 有效正文', render: (_: unknown, product: WorkbenchProduct) => `${product.success_count} / ${product.eligible_count}` },
          { title: '待标注', dataIndex: 'pending_count' }, { title: '失败', dataIndex: 'failed_count' }, { title: '已复核', dataIndex: 'reviewed_count' }, { title: '完成率', dataIndex: 'progress' },
        ]} locale={{ emptyText: '暂无模型标注数据' }} />
      </Card>
      <Card className="review-workbench-filter" title="筛选评论">
        <div className="review-workbench-filter-grid">
          <label>模型 / 提示词版本<Select value={profileId} onChange={value => { setProfileId(value); void loadOptions(value); }} options={options.profiles.map(profile => ({ value: profile.profile_id, label: `${profile.requested_model} · ${profile.prompt_version.slice(0, 32)}` }))} placeholder="选择模型版本" /></label>
          <label>商品<Select value={draftFilters.product_key || ''} onChange={value => updateFilter('product_key', value)} options={productOptions} /></label>
          <label>开始日期（UTC）<Input type="date" value={draftFilters.date_from || ''} onChange={event => updateFilter('date_from', event.target.value)} /></label>
          <label>结束日期（UTC）<Input type="date" value={draftFilters.date_to || ''} onChange={event => updateFilter('date_to', event.target.value)} /></label>
          <label>观点维度<Select value={draftFilters.aspect || ''} onChange={value => updateFilter('aspect', value)} options={mappingOptions(options.aspects, '全部维度')} /></label>
          <label>情感<Select value={draftFilters.sentiment || ''} onChange={value => updateFilter('sentiment', value)} options={mappingOptions(options.sentiments, '全部情感')} /></label>
          <label>处理状态<Select value={draftFilters.processing_status || ''} onChange={value => updateFilter('processing_status', value)} options={mappingOptions(options.processing_statuses, '全部状态')} /></label>
          <label>人工复核<Select value={draftFilters.human_status || ''} onChange={value => updateFilter('human_status', value)} options={mappingOptions(options.human_statuses, '全部状态')} /></label>
        </div>
        <Space wrap className="spaced"><Button type="primary" onClick={applyFilters}>应用筛选</Button><Button onClick={resetFilters}>重置</Button><label className="review-page-size">每页<Select value={pageSize} onChange={setPageSize} options={[10, 20, 50, 100].map(value => ({ value, label: `${value} 条` }))} /></label></Space>
      </Card>
      <div className="review-workbench-grid">
        <Card title={`评论与观点${reviews ? ` · 共 ${reviews.total} 条` : ''}`} loading={loading} className="review-workbench-list">
          {!reviews?.items.length ? <Empty description="没有符合条件的评论" /> : <div>{reviews.items.map(item => <article className={`review-workbench-card ${selectedId === item.id ? 'selected' : ''}`} key={item.id}>
            <div className="review-workbench-card-heading"><strong>#{item.id} · {item.product_id}</strong><Tag>{reviewStatusLabel(options, item.processing_status)}</Tag><Tag color={item.human_status === 'corrected' ? 'green' : item.human_status === 'confirmed' ? 'blue' : undefined}>{reviewStatusLabel(options, item.human_status)}</Tag></div>
            <p className="muted">{formatDate(item.published_at)} · 评分 {item.rating ?? '缺失'}</p><p className="review-workbench-body">{item.text || '（空正文）'}</p><OpinionList opinions={item.opinions} options={options} />
            <Button size="small" onClick={() => void openDetail(item.id)}>查看原文与复核</Button>
          </article>)}</div>}
          {reviews && <Pagination className="review-workbench-pagination" size="small" current={reviews.page} total={reviews.total} pageSize={reviews.page_size} showSizeChanger={false} onChange={value => void loadReviews(value)} />}
        </Card>
        <Card title="原文与人工复核" className="review-workbench-detail">
          {detailLoading ? <Skeleton active paragraph={{ rows: 10 }} /> : !detail ? <Empty description="从左侧选择一条评论" /> : <>
            <h3>#{detail.review.id} · {detail.review.product_id}</h3><p className="muted">{detail.review.product_title || '商品名称缺失'} · {formatDate(detail.review.published_at)}</p>
            <div className="review-workbench-body review-workbench-full-text" ref={textRef}><HighlightedText text={detail.review.text} opinion={highlighted} /></div>
            <h3>当前有效观点</h3><OpinionList opinions={detail.effective_opinions} options={options} onSelect={setHighlighted} />
            <details><summary>原文来源与模型记录</summary><p className="muted review-workbench-wrap">{detail.review.file_path} · 第 {detail.review.line_number} 行 · {detail.review.evidence_ref}</p>{detail.annotation && <><h4>模型原始观点</h4><OpinionList opinions={detail.annotation.opinions} options={options} onSelect={setHighlighted} /></>}<pre>{JSON.stringify(detail.model_history, null, 2)}</pre></details>
            {canEdit ? <>
              <h3>人工确认与纠错</h3><p className="muted">引用必须逐字存在于当前评论正文。删除全部观点表示人工判断没有可支持观点。</p>
              <label>复核人<Input value={reviewer} maxLength={80} onChange={event => setReviewer(event.target.value)} /></label>
              <label>备注<Input.TextArea value={note} maxLength={2000} rows={2} onChange={event => setNote(event.target.value)} /></label>
              <div className="review-workbench-editor">{opinions.map((opinion, index) => <div className="review-workbench-opinion-editor" key={index}>
                <Select value={opinion.aspect} onChange={value => updateOpinion(index, 'aspect', value)} options={Object.entries(options.aspects).map(([value, label]) => ({ value, label }))} />
                <Select value={opinion.sentiment} onChange={value => updateOpinion(index, 'sentiment', value)} options={Object.entries(options.sentiments).map(([value, label]) => ({ value, label }))} />
                <Input.TextArea value={opinion.quote} maxLength={4000} rows={2} placeholder="原文片段" onChange={event => updateOpinion(index, 'quote', event.target.value)} />
                <Button danger onClick={() => setOpinions(current => current.filter((_, currentIndex) => currentIndex !== index))}>删除观点</Button>
              </div>)}</div>
              <Space wrap><Button onClick={() => addOpinion()}>添加观点</Button><Button onClick={selectText}>用所选原文添加观点</Button></Space>
              <Space wrap className="spaced"><Button type="primary" loading={saving} icon={<SafetyCertificateOutlined />} onClick={() => void save('corrected')}>保存人工纠正</Button><Button loading={saving} icon={<CheckOutlined />} onClick={() => void save('confirmed')}>确认模型原始结果</Button></Space>
            </> : <p className="muted spaced">当前评论没有可复核的成功模型标注，请先完成模型标注。</p>}
            <details><summary>人工复核历史（{detail.human_reviews.length} 次）</summary>{detail.human_reviews.map(event => <div className="review-workbench-history" key={event.id}><strong>{options.human_statuses[event.action] || event.action} · {event.reviewer}</strong><p className="muted">{event.created_at} · 记录 {event.id}</p><p>{event.note}</p><OpinionList opinions={event.opinions} options={options} /></div>)}</details>
          </>}
        </Card>
      </div>
    </>}
  </section>;
}
