import { datasetKey, datasets } from '../../../analysis-draft';
import type { Options, Product } from '../../../types';

export function datasetSelectOptions(products: Product[]) {
  const available = datasets(products);
  return available.map(item => ({
    value: item.key,
    label: available.filter(other => other.version === item.version).length > 1
      ? `${item.version} · ${item.source}` : item.version,
  }));
}

export function productsForDataset(options: Options, dataset: string) {
  return options.products.filter(product => datasetKey(product) === dataset);
}

export function productSelectOptions(products: Product[]) {
  return products.map(product => ({
    value: product.product_id,
    label: `${product.product_title || product.product_id}（${product.review_count} 条）`,
  }));
}
