export const MAX_IMPORT_FILE_BYTES = 512 * 1024 * 1024;

export function validateImportFile(file: File, kind: 'reviews' | 'meta'): string | undefined {
  const supported = kind === 'reviews' ? /\.(?:csv|jsonl(?:\.gz)?)$/i : /\.jsonl(?:\.gz)?$/i;
  if (!supported.test(file.name) || file.size === 0 || file.size > MAX_IMPORT_FILE_BYTES) {
    return `请选择非空的 ${kind === 'reviews' ? '.csv、.jsonl 或 .jsonl.gz' : '.jsonl 或 .jsonl.gz'} 文件，单个文件最大 512 MiB。`;
  }
  return undefined;
}
