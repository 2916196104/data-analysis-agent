import { useCallback, useRef, useState } from 'react';
import { datasetApi } from '../../../shared/api/dataset-api';
import type { ImportParameters, TaskInput, UploadedImportFile } from '../../../types';
import { validateImportFile } from '../model/import-validation';

type FileKind = 'reviews' | 'meta';
type UploadEntry = { file: File; loaded: number };

interface UseImportTaskOptions {
  onSubmit: (input: TaskInput, note: string) => Promise<boolean>;
  onBusyChange: (busy: boolean) => void;
  onError: (message: string) => void;
}

export function useImportTask({ onSubmit, onBusyChange, onError }: UseImportTaskOptions) {
  const [reviewFile, setReviewFile] = useState<File>();
  const [metaFile, setMetaFile] = useState<File>();
  const [uploadAttempted, setUploadAttempted] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<Partial<Record<FileKind, UploadEntry>>>({});
  const [phase, setPhase] = useState('');
  const uploadedFiles = useRef<Partial<Record<FileKind, { file: File; uploaded: UploadedImportFile }>>>({});
  const controller = useRef<AbortController | undefined>(undefined);

  const selectFile = useCallback((file: File | undefined, kind: FileKind) => {
    if (!file) return;
    const validationError = validateImportFile(file, kind);
    if (validationError) { onError(validationError); return; }
    onError('');
    setUploadAttempted(false);
    if (kind === 'reviews') {
      setReviewFile(file);
      if (/\.csv$/i.test(file.name)) setMetaFile(undefined);
    } else setMetaFile(file);
  }, [onError]);

  const removeFile = useCallback((kind: FileKind) => {
    if (kind === 'reviews') setReviewFile(undefined); else setMetaFile(undefined);
    setUploadProgress(current => ({ ...current, [kind]: undefined }));
    delete uploadedFiles.current[kind];
  }, []);

  const upload = useCallback(async (file: File | undefined, kind: FileKind) => {
    if (!file) return undefined;
    const saved = uploadedFiles.current[kind];
    if (saved?.file === file) return saved.uploaded;
    setPhase(`正在上传${kind === 'reviews' ? '评论' : '元数据'}文件…`);
    const uploaded = await datasetApi.uploadImportFile(file, kind, loaded => {
      setUploadProgress(current => ({ ...current, [kind]: { file, loaded } }));
    }, controller.current?.signal);
    uploadedFiles.current[kind] = { file, uploaded };
    setUploadProgress(current => ({ ...current, [kind]: { file, loaded: file.size } }));
    return uploaded;
  }, []);

  const submitImport = useCallback(async (values: ImportParameters) => {
    if (!reviewFile && !metaFile) { onError('请先选择评论文件或商品元数据文件。'); return; }
    if (values.max_reviews != null && !reviewFile) { onError('设置新增评论上限时必须选择评论文件。'); return; }
    onError('');
    onBusyChange(true);
    setUploadAttempted(true);
    setUploadProgress({});
    controller.current = new AbortController();
    try {
      const uploadedReviews = await upload(reviewFile, 'reviews');
      const uploadedMeta = await upload(metaFile, 'meta');
      setPhase('正在提交导入任务…');
      const submitted = await onSubmit({ kind: 'import', parameters: {
        reviews_path: uploadedReviews?.path,
        meta_path: uploadedMeta?.path,
        dataset_version: values.dataset_version.trim(), product_ids: [], max_reviews: values.max_reviews ?? undefined,
      } }, `导入 ${reviewFile?.name || metaFile?.name}`);
      if (submitted) {
        setReviewFile(undefined); setMetaFile(undefined); setUploadAttempted(false); setUploadProgress({});
        uploadedFiles.current = {};
      }
    } catch (error) {
      if (!controller.current.signal.aborted) onError(error instanceof Error ? error.message : '导入请求失败，请稍后重试。');
    } finally {
      controller.current = undefined;
      onBusyChange(false);
      setPhase('');
    }
  }, [metaFile, onBusyChange, onError, onSubmit, reviewFile, upload]);

  const cancelUpload = useCallback(() => {
    controller.current?.abort();
    setPhase('');
    onBusyChange(false);
  }, [onBusyChange]);

  const reset = useCallback(() => {
    cancelUpload(); setReviewFile(undefined); setMetaFile(undefined); setUploadAttempted(false); setUploadProgress({}); uploadedFiles.current = {};
  }, [cancelUpload]);

  const files = (['reviews', 'meta'] as const).map(kind => ({ kind, file: kind === 'reviews' ? reviewFile : metaFile }));
  const uploadState = {
    total: files.reduce((sum, item) => sum + (item.file?.size ?? 0), 0),
    loaded: files.reduce((sum, item) => sum + (item.file ? uploadProgress[item.kind]?.loaded ?? 0 : 0), 0),
    confirmed: files.every(item => !item.file || uploadedFiles.current[item.kind]?.file === item.file),
    busy: !!controller.current, failed: false, detail: phase,
  };
  return { reviewFile, metaFile, uploadAttempted, uploadState, phase, selectFile, removeFile, submitImport, cancelUpload, reset };
}
