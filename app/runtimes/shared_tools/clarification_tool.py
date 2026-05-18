"""Runtime agent 的内置澄清工具。"""

from langchain.tools import tool

from app.runtimes.deep_agent.middleware.clarification_types import ClarificationType


@tool("ask_clarification", parse_docstring=True, return_direct=True)
def ask_clarification_tool(
    question: str,
    clarification_type: ClarificationType,
    context: str | None = None,
    options: list[str] | None = None,
) -> str:
    """在继续执行前向用户发起澄清。

    当当前请求在缺少用户补充信息的情况下无法安全继续时，使用这个工具。
    典型场景包括缺少关键信息、用户意图存在多种合理理解、存在多个候选对象需要用户选择、
    下一步存在风险需要确认，或者你想提出建议但需要用户先同意。

    Args:
        question: 要展示给用户的澄清问题。
        clarification_type: 这次澄清所属的类型。
        context: 可选的补充说明，用于解释为什么需要这次澄清。
        options: 可选的候选项列表，适用于需要用户选择的场景。

    Returns:
        一个占位字符串。实际澄清流程由 middleware 接管。
    """
    _ = (question, clarification_type, context, options)
    return "clarification_handled_by_middleware"
