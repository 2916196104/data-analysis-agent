-- 先停止所有标注 Worker、派发器和提交入口，导出需要保留的队列审计记录。
-- 此脚本只移除新增调度表；原始评论、模型标注、观点、人工复核和旧任务表保留。
BEGIN;
DROP TABLE label_workers;
DROP TABLE label_request_tasks;
DROP TABLE label_requests;
DROP TABLE label_outbox;
DROP TABLE label_run_tasks;
DROP TABLE label_tasks;
DROP TABLE label_runs;
DROP TABLE label_profiles;
DROP TABLE label_queue_schema;
COMMIT;
