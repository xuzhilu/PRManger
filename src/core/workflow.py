from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import InMemorySaver

from .state import PRReviewState
from src.agents.listener_agent import feishu_listener_node
from src.agents.splitter_agent import pr_splitter_node
from src.agents.git_review_agent import git_review_node
from src.agents.code_analyzer_agent import code_analyzer_node
from src.agents.context_collector_agent import context_collector_node
from src.agents.decision_agent import decision_node
from src.agents.aggregator_agent import pr_aggregator_node
from src.agents.feedback_agent import feishu_feedback_node

# ============================================================================
# PR处理子图
# ============================================================================

# 全局子图单例 
_SUBGRAPH_SINGLETON = None

def get_pr_review_subgraph():
    """
    获取PR审查子图单例
    使用延迟初始化模式，首次调用时创建，后续复用
    """
    global _SUBGRAPH_SINGLETON
    
    if _SUBGRAPH_SINGLETON is None:
        _SUBGRAPH_SINGLETON = _build_single_pr_review_subgraph()
        print("[子图初始化] ✓ PR审查子图已编译并缓存")
    
    return _SUBGRAPH_SINGLETON


def _build_single_pr_review_subgraph():
    """
    内部方法：构建单个PR审查子图
    包含完整的PR审查流程：Git检查 -> 代码分析 -> 上下文收集(迭代) -> 决策
    """
    builder = StateGraph(PRReviewState)
    
    # 添加子图节点
    builder.add_node("git_review", git_review_node)
    builder.add_node("code_analyzer", code_analyzer_node)
    builder.add_node("context_collector", context_collector_node)
    builder.add_node("decision", decision_node)
    
    # 定义子图内部路由
    def subgraph_routing(state: PRReviewState) -> str:
        """子图内部路由逻辑"""
        current_stage = state.get("current_stage", "")
        
        if current_stage == "code_analysis":
            return "code_analyzer"
        elif current_stage == "context_collection":
            return "context_collector"
        elif current_stage in ["analysis_complete", "decision"]:
            return "decision"
        elif current_stage == "feishu_feedback":
            # 子图完成，准备退出
            return END
        else:
            return END
    
    # 构建子图流程
    builder.add_edge(START, "git_review")
    
    # Git检查后进入代码分析
    builder.add_conditional_edges(
        "git_review",
        subgraph_routing,
        ["code_analyzer", END]
    )
    
    # 代码分析后可能需要上下文收集或直接决策
    builder.add_conditional_edges(
        "code_analyzer",
        subgraph_routing,
        ["context_collector", "decision", END]
    )
    
    # 上下文收集后返回代码分析（迭代深化）
    builder.add_conditional_edges(
        "context_collector",
        subgraph_routing,
        ["code_analyzer", END]
    )
    
    # 决策后结束子图
    builder.add_edge("decision", END)
    
    # 编译子图（只执行一次）
    compiled_graph = builder.compile()
    
    return compiled_graph


# ============================================================================
# 主图路由函数
# ============================================================================

def main_routing_func(state: PRReviewState) -> str:
    """主图路由函数 - 简化的路由逻辑"""
    current_stage = state.get("current_stage", "")
    
    routing_map = {
        "pr_split": "pr_splitter",
        "single_pr_review": "single_pr_processor",  # 单个PR处理
        "sub_pr_review": "sub_pr_processor",        # 子PR批量处理
        "aggregation": "pr_aggregator",
        "feishu_feedback": "feishu_feedback",
        "completed": END,
        # 错误处理
        "feishu_listener_failed": "feishu_feedback",
        "splitter_failed": "feishu_feedback",
        "aggregator_failed": "feishu_feedback",
    }
    
    return routing_map.get(current_stage, END)


# ============================================================================
# 主图处理节点 - 使用子图
# ============================================================================

async def single_pr_processor_node(state: PRReviewState) -> PRReviewState:
    """
    单个PR处理节点 - 直接调用子图
    性能优化：使用预编译的子图单例
    """
    print("\n" + "="*60)
    print("=== 单个PR审查（使用子图）===")
    print("="*60)
    
    # 获取预编译的子图单例（避免重复构建）
    subgraph = get_pr_review_subgraph()
    
    try:
        # 准备子图输入状态
        subgraph_input = {
            "pr_diff": state.get("pr_diff", ""),
            "pr_files": state.get("pr_files", []),
            "pr_size": state.get("pr_size", "small"),
            "pr_stats": state.get("pr_stats", {}),
            "is_sub_pr": False,  # 标记为主PR
            "source_branch": state.get("source_branch", ""),
            "target_branch": state.get("target_branch", ""),
            "repo_name": state.get("repo_name", ""),
            "parent_pr_id": state.get("parent_pr_id", ""),
            "feishu_user_id": state.get("feishu_user_id", "未知"),  # 传递飞书用户ID
            "feishu_user_name": state.get("feishu_user_name", "未知"),  # 传递飞书用户名
            "current_stage": "code_analysis",  # 从代码分析开始（git_review会被子图首先执行）
        }
        
        # 执行子图
        print("[单个PR] 进入审查子图...")
        result = await subgraph.ainvoke(subgraph_input)
        
        # 提取子图结果
        print("[单个PR] 子图执行完成")
        
        # 返回更新后的状态，准备进入反馈阶段
        return {
            **result,
            "current_stage": "feishu_feedback"
        }
        
    except Exception as e:
        print(f"[单个PR] 处理异常: {str(e)}")
        return {
            "current_stage": "feishu_feedback",
            "final_decision": "error",
            "code_issues": [f"处理异常: {str(e)[:200]}"]
        }


async def sub_pr_processor_node(state: PRReviewState) -> PRReviewState:
    """
    子PR批量处理节点 - 循环调用子图
    适用于需要拆分的PR，循环处理每个子PR
    性能优化：所有子PR共享同一个预编译的子图实例
    """
    print("\n" + "="*60)
    print("=== 子PR批量审查（循环使用子图）===")
    print("="*60)
    
    sub_prs = state.get("sub_prs", [])
    if not sub_prs:
        print("[错误] 未找到子PR列表")
        return {
            "current_stage": "aggregation",
            "sub_pr_results": []
        }
    
    print(f"[批量处理] 开始依次处理 {len(sub_prs)} 个子PR...")
    
    # 获取预编译的子图单例（所有子PR共享，避免重复构建）
    subgraph = get_pr_review_subgraph()
    
    # 顺序处理每个子PR
    sub_pr_results = []
    for i, sub_pr in enumerate(sub_prs, 1):
        print(f"\n[子PR {i}/{len(sub_prs)}] {sub_pr.get('title', f'SubPR-{i}')}")
        
        try:
            # 准备子PR的独立状态
            sub_pr_diff = sub_pr.get("diff", "")
            sub_pr_files = sub_pr.get("files", [])
            
            # 计算子PR统计信息
            lines_added = sum(1 for line in sub_pr_diff.split('\n') 
                            if line.startswith('+') and not line.startswith('+++'))
            lines_deleted = sum(1 for line in sub_pr_diff.split('\n') 
                              if line.startswith('-') and not line.startswith('---'))
            
            subgraph_input = {
                "pr_diff": sub_pr_diff,
                "pr_files": sub_pr_files,
                "pr_size": "small",
                "pr_stats": {
                    "files_count": len(sub_pr_files),
                    "additions": lines_added,
                    "deletions": lines_deleted,
                    "lines_changed": lines_added + lines_deleted,
                    "diff_size": len(sub_pr_diff)
                },
                "is_sub_pr": True,
                "parent_pr_id": state.get("parent_pr_id", ""),
                "source_branch": state.get("source_branch", ""),
                "target_branch": state.get("target_branch", ""),
                "repo_name": state.get("repo_name", ""),
                "feishu_user_id": state.get("feishu_user_id", "未知"),  # 传递飞书用户ID
                "feishu_user_name": state.get("feishu_user_name", "未知"),  # 传递飞书用户名
                "current_stage": "code_analysis",
            }
            
            # 执行子图
            print(f"[子PR {i}/{len(sub_prs)}] 进入审查子图...")
            result = await subgraph.ainvoke(subgraph_input)
            
            # 收集子PR结果
            sub_pr_result = {
                "title": sub_pr.get("title", f"SubPR-{i}"),
                "module": sub_pr.get("module", "unknown"),
                "final_decision": result.get("final_decision", "unknown"),
                "issues": result.get("code_issues", []) + result.get("rule_violations", []),
                # 详细信息
                "pr_diff": result.get("pr_diff", ""),
                "pr_stats": result.get("pr_stats", {}),
                "changed_files": result.get("changed_files", []),
                "changed_definitions": result.get("changed_definitions", []),
                "analysis_conclusion": result.get("analysis_conclusion", {}),
                "all_collected_context": result.get("all_collected_context", {}),
                "impact_chain": result.get("impact_chain", []),
                "code_check_passed": result.get("code_check_passed", False),
            }
            
            sub_pr_results.append(sub_pr_result)
            print(f"[子PR {i}/{len(sub_prs)}] ✓ 处理完成，决策: {sub_pr_result['final_decision']}")
            
        except Exception as e:
            print(f"[子PR {i}/{len(sub_prs)}] ✗ 处理失败: {str(e)[:100]}")
            sub_pr_results.append({
                "title": sub_pr.get('title', f'SubPR-{i}'),
                "module": sub_pr.get("module", "unknown"),
                "final_decision": "error",
                "issues": [f"处理异常: {str(e)[:200]}"]
            })
    
    print(f"\n[批量处理完成] {len(sub_pr_results)} 个子PR已完成审查")
    print("="*60 + "\n")
    
    return {
        "sub_pr_results": sub_pr_results,
        "current_stage": "aggregation"
    }


# ============================================================================
# 构建主工作流图
# ============================================================================

def build_pr_review_graph():
    """
    构建PR审查主工作流图
    
    流程设计：
    1. 飞书监听 -> PR拆分判断
    2. 单个PR -> 直接进入子图处理 -> 反馈
    3. 多个子PR -> 循环进入子图处理 -> 汇总 -> 反馈
    """
    builder = StateGraph(PRReviewState)
    
    # 添加主图节点
    builder.add_node("feishu_listener", feishu_listener_node)
    builder.add_node("pr_splitter", pr_splitter_node)
    builder.add_node("single_pr_processor", single_pr_processor_node)  # 单PR处理（使用子图）
    builder.add_node("sub_pr_processor", sub_pr_processor_node)        # 批量子PR处理（循环子图）
    builder.add_node("pr_aggregator", pr_aggregator_node)
    builder.add_node("feishu_feedback", feishu_feedback_node)
    
    # 构建主图流程
    builder.add_edge(START, "feishu_listener")
    
    # 监听器 -> 拆分器
    builder.add_conditional_edges(
        "feishu_listener",
        main_routing_func,
        ["pr_splitter", "feishu_feedback", END]
    )
    
    # 拆分器 -> 单PR处理 或 批量子PR处理
    builder.add_conditional_edges(
        "pr_splitter",
        main_routing_func,
        ["single_pr_processor", "sub_pr_processor", "feishu_feedback", END]
    )
    
    # 单PR处理 -> 直接反馈
    builder.add_conditional_edges(
        "single_pr_processor",
        main_routing_func,
        ["feishu_feedback", END]
    )
    
    # 批量子PR处理 -> 汇总
    builder.add_conditional_edges(
        "sub_pr_processor",
        main_routing_func,
        ["pr_aggregator", END]
    )
    
    # 汇总 -> 反馈
    builder.add_conditional_edges(
        "pr_aggregator",
        main_routing_func,
        ["feishu_feedback", END]
    )
    
    # 反馈 -> 结束
    builder.add_conditional_edges(
        "feishu_feedback",
        main_routing_func,
        [END]
    )
    
    # 编译主图
    checkpointer = InMemorySaver()
    graph = builder.compile(checkpointer=checkpointer)
    
    return graph
