from src.core.state import PRReviewState
from langgraph.config import get_stream_writer

def feishu_listener_node(state: PRReviewState) -> PRReviewState:
    """消息监听智能体"""
    print("=== 消息监听智能体 ===")
    writer = get_stream_writer()
    writer({"stage": "feishu_listener", "status": "started"})
    
    source_branch = state.get("source_branch")
    target_branch = state.get("target_branch")
    feishu_user_name = state.get("feishu_user_name", "未知")
    
    if not source_branch:
        return {
            "current_stage": "feishu_listener_failed",
            "feedback_message": "源分支信息缺失"
        }
    
    print(f"[INFO] 源分支: {source_branch}")
    print(f"[INFO] 目标分支: {target_branch}")
    print(f"[INFO] 提交者: {feishu_user_name}")
    writer({"source_branch": source_branch, "target_branch": target_branch, "feishu_user_name": feishu_user_name})
    
    return {
        "current_stage": "pr_split",
        "feishu_user_name": feishu_user_name  
    }
