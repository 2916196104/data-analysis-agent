import type { Quality, Task, UploadedImportFile } from '../../types';
import { request, uploadRequest } from './http-client';

export const datasetApi = {
  quality: () => request<Quality>('/quality'),
  uploadImportFile: (
    file: File,
    kind: UploadedImportFile['kind'],
    onProgress?: (loaded: number) => void,
  ): Promise<UploadedImportFile> => {
    const params = new URLSearchParams({ kind, filename: file.name });
    return uploadRequest<UploadedImportFile>(`/import-files?${params}`, file, onProgress);
  },
  deleteImportedData: (id: string) => request<{ task: Task; deletion: Record<string, number | number[]>; reused: boolean }>(
    `/tasks/${encodeURIComponent(id)}/imported-data`, { method: 'DELETE' },
  ),
};
