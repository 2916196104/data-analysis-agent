import type { Options } from '../../types';
import { request } from './http-client';

// options 一次返回商品、各数据集的首末日期及各标注版本全量覆盖率。
export const catalogApi = {
  options: () => request<Options>('/options'),
};
