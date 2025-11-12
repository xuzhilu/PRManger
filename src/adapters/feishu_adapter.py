"""
飞书适配器 - PR审查系统
功能：SDK实现，使用长连接模式
"""

import asyncio
import json
import uuid
from datetime import datetime
from typing import Dict
import time

# 飞书SDK导入
import lark_oapi as lark
from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody
from lark_oapi.api.im.v1.model import P2ImMessageReceiveV1

# 导入重构后的模块
from src.core.workflow import build_pr_review_graph
from src.utils.config import CONFIG
from src.utils.concurrency_manager import get_concurrency_manager
from src.utils.thread_safe_logger import log_info, log_error, log_warning


class MessageDeduplicator:
    """消息去重器 - 防止飞书消息重复推送导致的重复处理"""
    
    def __init__(self, cache_duration: int = 3600):
        """
        初始化消息去重器
        
        Args:
            cache_duration: 消息ID缓存时长(秒)，默认1小时
        """
        self.cache_duration = cache_duration
        # 消息ID缓存：{message_id: timestamp}
        self.message_cache: Dict[str, float] = {}
        # 用户请求节流：{(user_id, content_hash): timestamp}
        self.user_throttle: Dict[tuple, float] = {}
        # 节流时间窗口(秒)
        self.throttle_window = 30
        
    def is_duplicate_message(self, message_id: str) -> bool:
        """
        检查消息是否重复
        """
        current_time = time.time()
        
        # 清理过期缓存
        self._clean_expired_cache(current_time)
        
        # 检查是否已处理过
        if message_id in self.message_cache:
            cached_time = self.message_cache[message_id]
            log_warning(f"[消息去重] 检测到重复消息ID: {message_id} (首次接收: {datetime.fromtimestamp(cached_time).strftime('%H:%M:%S')})")
            return True
        
        # 记录新消息
        self.message_cache[message_id] = current_time
        return False
    
    def should_throttle_user(self, user_id: str, content: str) -> bool:
        """
        检查用户请求是否应被节流
        """
        current_time = time.time()
        
        # 生成内容hash用于去重
        content_hash = hash(content.strip())
        key = (user_id, content_hash)
        
        # 检查节流
        if key in self.user_throttle:
            last_time = self.user_throttle[key]
            time_diff = current_time - last_time
            
            if time_diff < self.throttle_window:
                log_warning(f"[用户节流] 用户 {user_id[:8]}... 在 {time_diff:.1f}秒内重复提交相同请求")
                return True
        
        # 更新节流记录
        self.user_throttle[key] = current_time
        return False
    
    def _clean_expired_cache(self, current_time: float):
        """清理过期的缓存记录"""
        # 清理消息ID缓存
        expired_messages = [
            msg_id for msg_id, timestamp in self.message_cache.items()
            if current_time - timestamp > self.cache_duration
        ]
        for msg_id in expired_messages:
            del self.message_cache[msg_id]
        
        # 清理用户节流缓存
        expired_throttles = [
            key for key, timestamp in self.user_throttle.items()
            if current_time - timestamp > self.cache_duration
        ]
        for key in expired_throttles:
            del self.user_throttle[key]
        
        if expired_messages or expired_throttles:
            log_info(f"[缓存清理] 清理了 {len(expired_messages)} 条消息缓存和 {len(expired_throttles)} 条节流记录")


class PRReviewManager:
    """PR审查管理器"""
    
    def __init__(self):
        self.reviews: Dict[str, Dict] = {}
        self.pr_graph = build_pr_review_graph()
        
    def add_review(self, review_data: Dict) -> str:
        review_id = str(uuid.uuid4())
        review_data['id'] = review_id
        review_data['created_at'] = datetime.now().isoformat()
        self.reviews[review_id] = review_data
        return review_id
    
    def get_review(self, review_id: str) -> Dict:
        return self.reviews.get(review_id)
    
    def update_review(self, review_id: str, update_data: Dict):
        if review_id in self.reviews:
            self.reviews[review_id].update(update_data)
    
    async def run_pr_review(self, review_id: str, initial_state: Dict) -> Dict:
        """运行PR审查工作流"""
        config = {"configurable": {"thread_id": review_id}}
        final_state = None
        try:
            async for chunk in self.pr_graph.astream(initial_state, config, stream_mode="values"):
                final_state = chunk
                log_info(f"[审查进度] {chunk.get('current_stage', 'unknown')}")
            return final_state
        except Exception as e:
            log_error(f"[错误] PR审查失败: {str(e)}")
            import traceback
            traceback.print_exc()
            return None


# 全局变量
review_manager = PRReviewManager()
message_deduplicator = MessageDeduplicator()

# 飞书配置
FEISHU_APP_ID = CONFIG['feishu_bot']['app_id']
FEISHU_APP_SECRET = CONFIG['feishu_bot']['app_secret']
FEISHU_ENCRYPT_KEY = CONFIG['feishu_bot'].get('encrypt_key')
FEISHU_VERIFICATION_TOKEN = CONFIG['feishu_bot'].get('verification_token')

# Git仓库配置
GIT_BASE_BRANCH = CONFIG['git_repo']['base_branch']
REPO_NAME = CONFIG['git_repo']['repo_name']
ADMIN_FEISHU_IDS = CONFIG['feishu_bot']['admins']

# 创建飞书客户端
client = lark.Client.builder() \
    .app_id(FEISHU_APP_ID) \
    .app_secret(FEISHU_APP_SECRET) \
    .log_level(lark.LogLevel.INFO) \
    .build()


def send_text_message(user_id: str, text: str):
    """发送文本消息（支持长消息自动分段）"""
    try:
        # 飞书单条消息限制约5000字符
        MAX_LENGTH = 3500
        
        if len(text) <= MAX_LENGTH:
            # 短消息直接发送
            request = CreateMessageRequest.builder() \
                .receive_id_type("open_id") \
                .request_body(CreateMessageRequestBody.builder()
                    .receive_id(user_id)
                    .msg_type("text")
                    .content(json.dumps({"text": text}))
                    .build()) \
                .build()
            
            response = client.im.v1.message.create(request)
            if not response.success():
                log_error(f"[错误] 发送消息失败: {response.code} - {response.msg}")
            return response
        else:
            # 长消息分段发送
            log_info(f"[信息] 消息过长({len(text)}字符)，分段发送")
            parts = []
            current_part = ""
            
            for line in text.split('\n'):
                if len(current_part) + len(line) + 1 > MAX_LENGTH:
                    # 当前部分已满，保存并开始新部分
                    if current_part:
                        parts.append(current_part)
                    current_part = line
                else:
                    if current_part:
                        current_part += '\n' + line
                    else:
                        current_part = line
            
            # 添加最后一部分
            if current_part:
                parts.append(current_part)
            
            # 发送所有部分
            log_info(f"[信息] 共分{len(parts)}段发送")
            for i, part in enumerate(parts, 1):
                prefix = f"[{i}/{len(parts)}]\n\n" if len(parts) > 1 else ""
                
                request = CreateMessageRequest.builder() \
                    .receive_id_type("open_id") \
                    .request_body(CreateMessageRequestBody.builder()
                        .receive_id(user_id)
                        .msg_type("text")
                        .content(json.dumps({"text": prefix + part}))
                        .build()) \
                    .build()
                
                response = client.im.v1.message.create(request)
                if not response.success():
                    log_error(f"[错误] 发送第{i}段消息失败: {response.code} - {response.msg}")
                else:
                    log_info(f"[成功] 已发送第{i}/{len(parts)}段")
                
                # 避免发送过快
                if i < len(parts):
                    import time
                    time.sleep(0.5)
            
            return response
            
    except Exception as e:
        log_error(f"[错误] 发送消息异常: {str(e)}")
        import traceback
        traceback.print_exc()


def process_pr_request_sync(message: str, sender_id: str):
    """处理PR审查请求（同步包装函数，用于线程池）"""
    asyncio.run(process_pr_request(message, sender_id))


def get_user_name(open_id: str) -> str:
    """获取飞书用户名"""
    try:
        from lark_oapi.api.contact.v3 import GetUserRequest
        
        log_info(f"[调试] 开始获取用户信息，open_id: {open_id}")
        
        request = GetUserRequest.builder() \
            .user_id_type("open_id") \
            .user_id(open_id) \
            .build()
        
        response = client.contact.v3.user.get(request)
        
        if response.success():
            user = response.data.user
            
            # 尝试多个字段
            user_name = getattr(user, 'name', None) or \
                       getattr(user, 'nickname', None) or \
                       getattr(user, 'en_name', None)
            
            if user_name:
                return user_name
            else:
                # 如果都没有，使用ID的后8位作为标识
                short_id = open_id[-8:] if len(open_id) > 8 else open_id
                return f"User_{short_id}"
        else:
            log_error(f"[错误] 获取用户信息失败")
            # 返回ID后8位作为fallback
            short_id = open_id[-8:] if len(open_id) > 8 else open_id
            return f"User_{short_id}"
    except Exception as e:
        log_error(f"[错误] 获取用户名异常: {str(e)}")
        import traceback
        log_error(traceback.format_exc())
        # 返回ID后8位作为fallback
        short_id = open_id[-8:] if len(open_id) > 8 else open_id
        return f"User_{short_id}"


async def process_pr_request(message: str, sender_id: str):
    """处理PR审查请求（异步函数）"""
    import re
    
    # 获取用户名
    user_name = get_user_name(sender_id)
    log_info(f"[用户] {user_name} ({sender_id})")
    
    # 解析消息格式："分支名 merge 目标分支" 或 "分支名"
    source_branch = None
    target_branch = GIT_BASE_BRANCH
    
    # 尝试匹配 "分支名 merge 目标分支" 格式
    match = re.search(r'(\S+)\s+merge\s+(\S+)', message, re.IGNORECASE)
    if match:
        source_branch = match.group(1)
        target_branch = match.group(2)
    
    if not source_branch:
        send_text_message(sender_id, 
            "❌ 无法解析消息\n\n"
            "请使用以下格式之一：\n"
            "1. 分支名 merge 目标分支\n"
            "示例：feature/login-fix merge main"
        )
        return
    
    review_id = review_manager.add_review({
        'feishu_user_id': sender_id,
        'feishu_message': message,
        'source_branch': source_branch,
        'target_branch': target_branch,
        'repo_name': REPO_NAME
    })
    
    send_text_message(sender_id, 
        f"✅ 已收到分支合并审查请求\n\n"
        f"仓库: {REPO_NAME}\n"
        f"源分支: {source_branch}\n"
        f"目标分支: {target_branch}\n\n"
        f"正在审查中..."
    )
    
    initial_state = {
        'feishu_user_id': sender_id,
        'feishu_user_name': user_name,  # 添加用户名
        'feishu_message': message,
        'source_branch': source_branch,
        'target_branch': target_branch,
        'repo_name': REPO_NAME,
    }
    
    log_info(f"[信息] 开始审查分支合并: {review_id}")
    final_state = await review_manager.run_pr_review(review_id, initial_state)
    
    if final_state:
        # 获取双重反馈
        submitter_feedback = final_state.get('submitter_feedback', '')
        admin_feedback = final_state.get('admin_feedback', '')
        
        # 发送给提交者
        if submitter_feedback:
            send_text_message(sender_id, submitter_feedback)
        
        # 发送给管理员
        if admin_feedback:
            for admin_id in ADMIN_FEISHU_IDS:
                send_text_message(admin_id, admin_feedback)
            
    else:
        send_text_message(sender_id, "❌ 审查出错，请联系管理员")
        error_report = f"⚠️ 分支合并审查系统错误\n\n"
        error_report += f"仓库: {REPO_NAME}\n"
        error_report += f"分支: {source_branch} -> {target_branch}\n"
        error_report += f"提交人飞书ID: {sender_id}\n"
        error_report += f"原始消息: {message}\n\n"
        error_report += f"请检查系统日志"
        for admin_id in ADMIN_FEISHU_IDS:
            send_text_message(admin_id, error_report)


def do_im_message_receive_v1(data: P2ImMessageReceiveV1) -> None:
    """处理消息接收事件"""
    try:
        message = data.event.message
        sender = data.event.sender
        
        if message.message_type == "text":
            # 获取消息ID（用于去重）
            message_id = message.message_id
            
            # 消息去重检查
            if message_deduplicator.is_duplicate_message(message_id):
                log_warning(f"[消息去重] 忽略重复消息: {message_id}")
                return
            
            content = json.loads(message.content)
            text = content.get("text", "")
            sender_id = sender.sender_id.open_id
            
            log_info(f"[消息] {sender_id}: {text}")
            
            if any(kw in text for kw in ["提交", "merge", "合并", "审查"]):
                # 用户请求节流检查
                if message_deduplicator.should_throttle_user(sender_id, text):
                    throttle_msg = (
                        f"⚠️ 请求过于频繁\n\n"
                        f"检测到您在短时间内提交了相同的请求。\n"
                        f"请等待 {message_deduplicator.throttle_window} 秒后再试。"
                    )
                    send_text_message(sender_id, throttle_msg)
                    log_warning(f"[用户节流] 已拒绝用户 {sender_id} 的重复请求")
                    return
                
                # 使用并发控制管理器提交任务
                manager = get_concurrency_manager()
                success, msg = manager.submit_task(
                    process_pr_request_sync,
                    text,
                    sender_id,
                    task_name=f"PR_Review_{sender_id[:8]}"
                )
                
                if not success:
                    # 队列已满，通知用户稍后再试
                    stats = manager.get_stats()
                    reject_message = (
                        f"⚠️ 系统繁忙，请稍后再试\n\n"
                        f"当前状态：\n"
                        f"🔄 正在处理: {stats['current_processing']}\n"
                        f"⏳ 队列等待: {stats['current_queued']}\n\n"
                        f"请稍后再试"
                    )
                    send_text_message(sender_id, reject_message)
                    log_warning(f"[并发控制] 已拒绝用户 {sender_id} 的请求 - {msg}")
                else:
                    # 任务已加入队列，通知用户
                    stats = manager.get_stats()
                    
                    # 只有当有任务在排队时才通知（排除立即处理的情况）
                    if stats['current_queued'] > 0:
                        queue_message = (
                            f"✅ 请求已接受\n\n"
                            f"📊 当前系统状态：\n"
                            f"🔄 正在处理: {stats['current_processing']} 个任务\n"
                            f"⏳ 队列等待: {stats['current_queued']} 个任务\n\n"
                            f"您的请求已加入队列（第 {stats['current_queued']} 位），\n"
                            f"请耐心等待，处理完成后会通知您。"
                        )
                        send_text_message(sender_id, queue_message)
                    
                    log_info(f"[并发控制] 已接受用户 {sender_id} 的请求 - {msg}")
    
    except Exception as e:
        log_error(f"[错误] 处理消息失败: {str(e)}")
        import traceback
        traceback.print_exc()


def start_feishu_bot():
    """启动飞书机器人"""
    print("="*60)
    print("飞书PR管理系统")
    print("="*60)
    
    if not FEISHU_ENCRYPT_KEY or not FEISHU_VERIFICATION_TOKEN:
        print("!! 致命错误: 'FEISHU_ENCRYPT_KEY' 或 'FEISHU_VERIFICATION_TOKEN' 未设置。")
        print("!! 请在 config/config.yaml 或环境变量中设置它们。")
        print("!! 程序退出。")
        print("="*60)
        return

    # 初始化并发控制管理器（打印配置信息）
    manager = get_concurrency_manager()
    
    print(f"\n应用ID: {FEISHU_APP_ID}")
    print(f"仓库名称: {REPO_NAME}")
    print(f"基础分支: {GIT_BASE_BRANCH}")
    print(f"管理员: {', '.join(ADMIN_FEISHU_IDS)}")
    
    # 显示并发控制配置
    concurrency_config = CONFIG['feishu_bot'].get('concurrency', {})
    if concurrency_config.get('enabled', True):
        print(f"\n并发控制:")
        print(f"  - 最大并发处理: {concurrency_config.get('max_workers', 4)} 个任务")
        print(f"  - 队列容量: {concurrency_config.get('max_queue_size', 10)} 个任务")
        print(f"  - 状态: 已启用 ✓")
    else:
        print(f"\n并发控制: 已禁用（无限制）⚠️")
    
    # 显示消息去重配置
    print(f"\n消息去重保护:")
    print(f"  - 消息ID去重: 已启用（缓存时长: {message_deduplicator.cache_duration}秒）")
    print(f"  - 用户请求节流: 已启用（节流窗口: {message_deduplicator.throttle_window}秒）")
    print(f"  - 防止飞书消息重复推送 ✓")
    
    print("\n核心功能:")
    print("1. 深度依赖分析 - 实际搜索代码使用情况")
    print("2. 删除检测 - 检测被删除的函数/类是否还在被使用")
    print("3. 精准识别 - 区分确定性问题和潜在风险")
    print("4. 智能规模评估 - 根据PR规模调整分析策略")
    print("5. 并发控制 - 防止系统过载，队列管理")
    print("6. 消息去重 - 防止重复处理相同请求")
    print("\n使用说明:")
    print("1. 用户发送消息触发分支合并审查")
    print("2. 格式：'分支名 merge 目标分支'")
    print("3. 系统自动进行深度影响分析")
    print("4. 发现确定性问题→自动拒绝")
    print("5. 无确定性问题→通知管理员人工审核")
    print("6. 系统繁忙时会提示用户稍后再试")
    print("="*60)
    print("\n启动长连接...")
    
    handler = lark.EventDispatcherHandler.builder(
        FEISHU_ENCRYPT_KEY,
        FEISHU_VERIFICATION_TOKEN
    ).register_p2_im_message_receive_v1(do_im_message_receive_v1).build()
    
    # 启动长连接客户端
    cli = lark.ws.Client(FEISHU_APP_ID, FEISHU_APP_SECRET, event_handler=handler)
    cli.start()
