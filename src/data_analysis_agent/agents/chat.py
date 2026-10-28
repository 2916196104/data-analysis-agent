"""基础单轮问答；复用模型配置，不查询业务数据或创建分析任务。"""

from data_analysis_agent.core.config import AgentSettings
from data_analysis_agent.core.validation import validate_text


SYSTEM_PROMPT = """你是观数商品评论分析系统的基础 AI 助手，请用中文清楚回答用户的问题。
本模式只提供普通问答和一般知识解释，没有商品、评论、标注、统计或原文查询工具，
也没有读取当前页面的商品和数据范围。不知道以前的聊天内容，只回答本轮问题。
不得声称已经访问数据库、查询真实商品数据或执行分析；不得编造评论数量、差评率、
问题分布、评论原文或数据来源。用户要求查询这些业务数据时，明确说明当前模式不能
执行查询，并提示使用系统已有的评论分析功能。区分一般知识与系统实际查询得到的事实。
"""


class ChatError(RuntimeError):
    """可安全返回给客户端的模型调用错误。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def answer_message(message: str, *, settings: AgentSettings) -> str:
    """返回实际模型文本；SDK 异常和不完整输出不能冒充成功回答。"""
    # 延迟加载 SDK，保持 API 组装及原有任务提交的依赖边界。
    from openai import (
        APIConnectionError, APIError, APITimeoutError, AuthenticationError, OpenAI, RateLimitError,
    )

    question = validate_text(message, "问题", maximum=4000)
    try:
        with OpenAI(api_key=settings.api_key, base_url=settings.base_url,
                    timeout=settings.timeout_seconds, max_retries=settings.max_retries) as client:
            response = client.chat.completions.create(
                model=settings.model,
                messages=[{"role": "system", "content": SYSTEM_PROMPT},
                          {"role": "user", "content": question}],
            )
    except APITimeoutError as exc:
        raise ChatError("model_timeout", "模型回答超时，请稍后重新发送。") from exc
    except AuthenticationError as exc:
        raise ChatError("authentication_error", "模型服务认证失败，请检查后端 API 密钥配置。") from exc
    except RateLimitError as exc:
        raise ChatError("rate_limit", "模型服务请求受限，请稍后重新发送。") from exc
    except APIConnectionError as exc:
        raise ChatError("model_connection_error", "无法连接模型服务，请检查后端服务地址和网络。") from exc
    except APIError as exc:
        raise ChatError("model_api_error", "模型服务调用失败，请检查后端模型配置或稍后重试。") from exc

    choices = getattr(response, "choices", None)
    choice = choices[0] if isinstance(choices, list) and choices else None
    reply = getattr(choice, "message", None)
    content = getattr(reply, "content", None)
    if (getattr(choice, "finish_reason", None) != "stop" or getattr(reply, "tool_calls", None)
            or not isinstance(content, str) or not content.strip()):
        raise ChatError("invalid_model_response", "模型未返回完整的文本回答，请重新发送。")
    return content.strip().replace(settings.api_key, "[REDACTED]")
