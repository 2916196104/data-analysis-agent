import type { ComponentProps } from 'react';
import { Alert } from 'antd';
import LabelForm from '../../LabelForm';
import './annotation.css';

type AnnotationPageProps = ComponentProps<typeof LabelForm> & { error: string };

export default function AnnotationPage({ error, ...formProps }: AnnotationPageProps) {
  return <section className="annotation-page" aria-labelledby="annotation-title">
    <div className="review-page-heading"><div><h1 id="annotation-title">评论标注</h1><p>选择待处理的评论，提取观点供后续分析使用。</p></div></div>
    <div className="annotation-form-content">
      {error && <Alert className="spaced-bottom" type="error" showIcon message="标注任务未提交" description={error} />}
      <LabelForm {...formProps} />
    </div>
    <p className="muted spaced">人工确认与纠错请使用 <a href={import.meta.env.VITE_REVIEW_WORKBENCH_URL || 'http://127.0.0.1:8765'} target="_blank" rel="noreferrer">独立人工复核工作台</a>（需先启动原有服务）。</p>
  </section>;
}
