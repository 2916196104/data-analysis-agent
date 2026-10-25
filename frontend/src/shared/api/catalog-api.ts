import type { Options } from '../../types';
import { request } from './http-client';

// 商品、数据集和标注版本由现有 options 一次返回，不另发商品请求。
export const catalogApi = {
  options: () => request<Options>('/options'),
};
