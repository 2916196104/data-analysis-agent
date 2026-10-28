import { beforeEach, describe, expect, it, vi } from 'vitest';
import { request } from '../../../api';
import { workbenchApi } from './workbench-api';

vi.mock('../../../api', async () => {
  const actual = await vi.importActual<typeof import('../../../api')>('../../../api');
  return { ...actual, request: vi.fn() };
});

describe('人工复核 API 适配器', () => {
  beforeEach(() => vi.clearAllMocks());

  it('保留筛选查询参数并读取 /options 响应', async () => {
    vi.mocked(request).mockResolvedValueOnce({ selected_profile_id: 7 } as never);

    await expect(workbenchApi.options(7)).resolves.toEqual({ selected_profile_id: 7 });
    expect(request).toHaveBeenCalledWith('/options?profile_id=7');
  });

  it('使用人工复核接口保存确认或纠正请求体', async () => {
    vi.mocked(request).mockResolvedValueOnce({ revision_id: 11, action: 'corrected', opinion_count: 1 } as never);

    await workbenchApi.saveHumanReview(42, {
      annotation_id: 10,
      expected_revision_id: null,
      action: 'corrected',
      reviewer: '测试复核员',
      note: '引用与正文一致',
      opinions: [{ aspect: 'quality', sentiment: 'negative', quote: '包装破损' }],
    });

    expect(request).toHaveBeenCalledWith('/reviews/42/human-reviews', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({
        annotation_id: 10,
        expected_revision_id: null,
        action: 'corrected',
        reviewer: '测试复核员',
        note: '引用与正文一致',
        opinions: [{ aspect: 'quality', sentiment: 'negative', quote: '包装破损' }],
      }),
    }));
  });
});
