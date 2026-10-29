import { useEffect, useState } from 'react';
import { reviewApi } from '../../../shared/api/review-api';
import type { Page, Review } from '../../../types';
import { errorMessage } from '../../../shared/api/http-client';

export function useDatasetPreview(selectedProductKey: string | undefined, profileId?: number) {
  const [page, setPage] = useState(1);
  const [data, setData] = useState<Page<Review>>();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    if (!selectedProductKey) return;
    const controller = new AbortController();
    setLoading(true); setError(''); setData(undefined);
    reviewApi.reviews(selectedProductKey, profileId, page, controller.signal)
      .then(result => { if (!controller.signal.aborted) setData(result); })
      .catch(requestError => { if (!controller.signal.aborted) setError(errorMessage(requestError)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [page, profileId, revision, selectedProductKey]);
  return { page, setPage, data, loading, error, retry: () => setRevision(value => value + 1) };
}
