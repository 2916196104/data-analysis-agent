import type { Page, Review } from '../../types';
import { query, request } from './http-client';

// 这里只读 /api/v1 的评论；独立人工复核工作台的 /api 与 CSRF 协议不复用此层。
export const reviewApi = {
  reviews: (
    productKey: string,
    profileId: number | undefined,
    page: number,
    signal?: AbortSignal,
  ) => request<Page<Review>>(
    `/reviews${query({ product_key: productKey, profile_id: profileId, page, page_size: 8 })}`,
    { signal },
  ),
  review: (id: number, profileId?: number, signal?: AbortSignal) => request<Record<string, unknown>>(`/reviews/${id}${query({ profile_id: profileId })}`, { signal }),
};
