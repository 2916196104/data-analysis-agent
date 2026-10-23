"""提交或重试前校验队列文件和模型身份。"""

from pathlib import Path

from data_analysis_agent.core.config import AgentSettings, read_settings
from data_analysis_agent.core.errors import ReviewDataError
from data_analysis_agent.jobs.parameters import PARAMETERS
from data_analysis_agent.jobs.store import TaskConflict, enqueue, get_task
from data_analysis_agent.reviews.prompts import PROMPT_VERSION


def import_path(value, root):
    """队列中的导入只能使用配置导入目录内的文件。"""
    root = Path(root).resolve()
    candidate = Path(value)
    path = (candidate if candidate.is_absolute() else root / candidate).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ReviewDataError("导入文件不存在或不在服务配置的 import-root 内。")
    if not (path.name.endswith(".jsonl") or path.name.endswith(".jsonl.gz")):
        raise ReviewDataError("只支持 .jsonl 或 .jsonl.gz 导入文件。")
    return str(path)


def configured_identity(env_file):
    settings = AgentSettings.from_values(read_settings(env_file))
    return settings, {"model": settings.model, "base_url": settings.base_url, "prompt_version": PROMPT_VERSION}


def prepare_parameters(kind, parameters, *, import_root, env_file):
    values = PARAMETERS[kind].model_validate(parameters).model_dump()
    if kind == "import":
        for name in ("reviews_path", "meta_path"):
            if values[name] is not None:
                values[name] = import_path(values[name], import_root)
    elif kind == "label":
        _, values["model_identity"] = configured_identity(env_file)
    return values


def retry_task(task_db, task_id, *, import_root, env_file, idempotency_key=None):
    task = get_task(task_db, task_id)
    if task["status"] not in ("failed", "partial"):
        raise TaskConflict("只能重试失败或部分完成的任务。")
    parameters = dict(task["parameters"])
    if task["kind"] == "label":
        _, identity = configured_identity(env_file)
        if parameters["model_identity"] != identity:
            raise TaskConflict("模型配置已变化，请使用新配置提交新标注任务。")
    elif task["kind"] == "import":
        for name in ("reviews_path", "meta_path"):
            if parameters.get(name) is not None:
                import_path(parameters[name], import_root)
    return enqueue(task_db, task["kind"], parameters,
                   idempotency_key=idempotency_key, parent_task_id=task_id)
