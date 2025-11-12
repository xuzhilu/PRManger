"""
并发控制管理器
用于管理PR审查请求的并发处理和任务队列
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from queue import Queue, Full
from threading import Lock
from datetime import datetime
from typing import Callable, Any, Optional
from .config import CONFIG
from .thread_safe_logger import log_info, log_error, log_warning, set_task_context, clear_task_context


class ConcurrencyManager:
    """并发控制管理器"""
    
    def __init__(self):
        """初始化并发控制管理器"""
        # 读取配置
        concurrency_config = CONFIG['feishu_bot'].get('concurrency', {})
        self.enabled = concurrency_config.get('enabled', True)
        self.max_workers = concurrency_config.get('max_workers', 4)
        self.max_queue_size = concurrency_config.get('max_queue_size', 10)
        
        # 线程池
        self.executor = ThreadPoolExecutor(
            max_workers=self.max_workers,
            thread_name_prefix="PR_Review_Worker"
        )
        
        # 任务队列
        self.task_queue = Queue(maxsize=self.max_queue_size)
        
        # 统计信息
        self.stats_lock = Lock()
        self.stats = {
            'total_received': 0,      # 总接收请求数
            'total_processed': 0,     # 总处理完成数
            'total_rejected': 0,      # 总拒绝数（队列满）
            'current_processing': 0,   # 当前正在处理数
            'current_queued': 0,       # 当前队列中数量
        }
        
        # 启动队列处理线程
        if self.enabled:
            self._start_queue_processor()
            log_info(f"[并发控制] 已启用 - 最大并发: {self.max_workers}, 队列容量: {self.max_queue_size}")
        else:
            log_warning("[并发控制] 已禁用 - 无并发限制")
    
    def _start_queue_processor(self):
        """启动队列处理线程"""
        def process_queue():
            """持续处理队列中的任务"""
            while True:
                try:
                    # 从队列获取任务（阻塞等待）
                    task_info = self.task_queue.get()
                    
                    if task_info is None:  # 停止信号
                        break
                    
                    task_func, args, task_id = task_info
                    
                    # 更新统计
                    with self.stats_lock:
                        self.stats['current_queued'] -= 1
                        self.stats['current_processing'] += 1
                    
                    log_info(f"[并发控制] 开始处理任务 {task_id} (活跃: {self.stats['current_processing']}, 队列: {self.stats['current_queued']})")
                    
                    try:
                        # 设置任务上下文（用于日志前缀）
                        set_task_context(task_id, task_id.split('_')[0])
                        
                        # 提交到线程池执行
                        future = self.executor.submit(task_func, *args)
                        future.result()  # 等待完成
                        
                        with self.stats_lock:
                            self.stats['total_processed'] += 1
                        
                        log_info(f"[并发控制] 任务 {task_id} 完成")
                        
                    except Exception as e:
                        log_error(f"[并发控制] 任务 {task_id} 执行异常: {str(e)}")
                        import traceback
                        traceback.print_exc()
                    
                    finally:
                        # 清除任务上下文
                        clear_task_context()
                        
                        # 更新统计
                        with self.stats_lock:
                            self.stats['current_processing'] -= 1
                        
                        # 标记任务完成
                        self.task_queue.task_done()
                        
                except Exception as e:
                    log_error(f"[并发控制] 队列处理异常: {str(e)}")
                    import traceback
                    traceback.print_exc()
        
        # 使用固定数量的队列处理线程（等于max_workers）
        import threading
        for i in range(self.max_workers):
            thread = threading.Thread(
                target=process_queue,
                name=f"Queue_Processor_{i}",
                daemon=True
            )
            thread.start()
    
    def submit_task(
        self,
        task_func: Callable,
        *args,
        task_name: str = "unnamed_task"
    ) -> tuple[bool, str]:
        """
        提交任务到队列
        
        Args:
            task_func: 任务函数
            *args: 任务函数的参数
            task_name: 任务名称（用于日志）
        
        Returns:
            (成功标志, 消息)
            - (True, "任务已提交") - 成功
            - (False, "队列已满") - 队列满
        """
        # 如果未启用并发控制，直接在新线程中执行
        if not self.enabled:
            import threading
            thread = threading.Thread(target=task_func, args=args)
            thread.start()
            return True, "任务已提交（无并发限制）"
        
        # 生成任务ID
        task_id = f"{task_name}_{datetime.now().strftime('%H%M%S_%f')}"
        
        # 更新统计
        with self.stats_lock:
            self.stats['total_received'] += 1
        
        # 尝试将任务加入队列
        try:
            self.task_queue.put_nowait((task_func, args, task_id))
            
            with self.stats_lock:
                self.stats['current_queued'] += 1
            
            current_queue = self.stats['current_queued']
            current_processing = self.stats['current_processing']
            
            log_info(f"[并发控制] 任务 {task_id} 已加入队列 (活跃: {current_processing}/{self.max_workers}, 队列: {current_queue}/{self.max_queue_size})")
            
            return True, f"任务已加入队列 (位置: {current_queue}/{self.max_queue_size})"
            
        except Full:
            # 队列已满
            with self.stats_lock:
                self.stats['total_rejected'] += 1
            
            log_warning(f"[并发控制] 任务 {task_id} 被拒绝 - 队列已满")
            
            return False, "系统繁忙，队列已满"
    
    def get_stats(self) -> dict:
        """获取统计信息"""
        with self.stats_lock:
            return self.stats.copy()
    
    def get_status_message(self) -> str:
        """获取状态消息（用于显示）"""
        stats = self.get_stats()
        
        if not self.enabled:
            return "并发控制: 已禁用"
        
        return (
            f"📊 系统状态\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🔄 正在处理: {stats['current_processing']}/{self.max_workers}\n"
            f"⏳ 队列中: {stats['current_queued']}/{self.max_queue_size}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📈 总接收: {stats['total_received']}\n"
            f"✅ 已完成: {stats['total_processed']}\n"
            f"❌ 已拒绝: {stats['total_rejected']}"
        )
    
    def shutdown(self, wait: bool = True):
        """关闭管理器"""
        log_info("[并发控制] 正在关闭...")
        
        if self.enabled:
            # 发送停止信号
            for _ in range(self.max_workers):
                self.task_queue.put(None)
        
        # 关闭线程池
        self.executor.shutdown(wait=wait)
        log_info("[并发控制] 已关闭")


# 全局单例
_concurrency_manager: Optional[ConcurrencyManager] = None


def get_concurrency_manager() -> ConcurrencyManager:
    """获取并发控制管理器单例"""
    global _concurrency_manager
    if _concurrency_manager is None:
        _concurrency_manager = ConcurrencyManager()
    return _concurrency_manager
