"""
飞书PR管理系统主程序
基于深度影响分析的智能代码审查系统
"""

from src.adapters.feishu_adapter import start_feishu_bot


if __name__ == "__main__":
    start_feishu_bot()
