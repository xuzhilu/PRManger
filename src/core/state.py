"""
PR审查系统状态定义
"""

from typing import TypedDict, List, Dict


class PRReviewState(TypedDict):
    """分支合并审查流程状态（原生Git模式）"""
    # 飞书相关
    feishu_user_id: str
    feishu_user_name: str
    feishu_message: str
    
    # 分支信息
    source_branch: str
    target_branch: str
    repo_name: str
    branch_title: str
    branch_author: str
    
    # 分支内容
    pr_diff: str
    pr_files: List[Dict]
    
    # 规模信息
    pr_size: str
    pr_stats: Dict
    
    # 审查结果
    code_check_passed: bool
    code_issues: List[str]
    
    # 流程控制
    current_stage: str
    final_decision: str
    feedback_message: str  # 用于错误反馈
    submitter_feedback: str  # 提交者反馈
    admin_feedback: str  # 管理员反馈
    
    # 智能体协作字段
    changed_files: List[str]  # 修改的文件列表
    context_request: Dict  # 上下文收集请求（包含search_items）
    context_response: Dict  # 上下文收集响应（包含dependencies）
    analysis_conclusion: Dict  # 分析结论
    
    # 迭代式深度分析字段
    iteration_count: int  # 当前迭代次数
    impact_chain: List[Dict]  # 影响链记录（包含每轮搜索的项）
    all_collected_context: Dict  # 累积的所有上下文信息（缓存）
    
    # PR拆分相关字段
    needs_split: bool  # 是否需要拆分
    sub_prs: List[Dict]  # 拆分后的子PR列表
    sub_pr_results: List[Dict]  # 子PR的审查结果列表
    is_sub_pr: bool  # 标识当前是否为子PR
    parent_pr_id: str  # 父PR标识（用于子PR）
    
    # AST缓存字段
    ast_cache: Dict[str, List]  # 文件路径 -> AST节点列表的缓存
