"""
PR审查系统
基于深度影响分析的智能代码审查系统
"""

__version__ = "2.0.0"

# 核心模块
from .core.workflow import build_pr_review_graph
from .core.state import PRReviewState

# 适配器
from .adapters.git_adapter import NativeGitAdapter

# 工具
from .utils.config import CONFIG, load_config, load_code_rules
from .utils.llm import llm, OLLAMA_MODEL
from .utils.helpers import calculate_pr_size

__all__ = [
    # 核心
    'build_pr_review_graph',
    'PRReviewState',
    # 适配器
    'NativeGitAdapter',
    # 配置
    'CONFIG',
    'load_config',
    'load_code_rules',
    # LLM
    'llm',
    'OLLAMA_MODEL',
    # 工具函数
    'calculate_pr_size',
]
