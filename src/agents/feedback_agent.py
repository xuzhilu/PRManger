from langgraph.config import get_stream_writer
from src.core.state import PRReviewState

def feishu_feedback_node(state: PRReviewState) -> PRReviewState:
    """飞书反馈智能体 - 发送双重反馈"""
    print("=== 飞书反馈智能体 ===")
    writer = get_stream_writer()
    writer({"stage": "feishu_feedback", "status": "started"})
    
    feishu_user_id = state.get("feishu_user_id", "") 
    submitter_feedback = state.get("submitter_feedback", "")
    admin_feedback = state.get("admin_feedback", "")
    
    # 发送提交者反馈
    print(f"\n{'='*60}")
    print(f"📤 发送提交者反馈给用户 {feishu_user_id}:")
    print(f"{'-'*60}")
    print(submitter_feedback)
    print(f"{'='*60}\n")
    
    # 发送管理员反馈（在这里打印，实际发送由飞书适配器完成）
    print(f"\n{'='*60}")
    print(f"📤 生成管理员反馈:")
    print(f"{'-'*60}")
    print(admin_feedback)
    print(f"{'='*60}\n")
    
    writer({
        "feishu_message_sent": True, 
        "submitter_notified": True,
        "admin_notified": True,
        "recipient": feishu_user_id
    })
    
    return {
        "current_stage": "completed"
    }
