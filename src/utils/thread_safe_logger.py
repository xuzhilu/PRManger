"""
线程安全的日志工具
解决多线程环境下日志输出混乱的问题
"""

import threading
from typing import Optional
from datetime import datetime
import sys


class ThreadSafeLogger:
    """线程安全的日志记录器"""
    
    def __init__(self):
        """初始化线程安全日志记录器"""
        self._lock = threading.Lock()
        self._task_contexts = {}  # {thread_id: task_info}
        
    def set_task_context(self, task_id: str, task_name: str):
        """
        为当前线程设置任务上下文
        
        Args:
            task_id: 任务ID
            task_name: 任务名称
        """
        thread_id = threading.get_ident()
        self._task_contexts[thread_id] = {
            'task_id': task_id,
            'task_name': task_name,
            'start_time': datetime.now()
        }
    
    def clear_task_context(self):
        """清除当前线程的任务上下文"""
        thread_id = threading.get_ident()
        if thread_id in self._task_contexts:
            del self._task_contexts[thread_id]
    
    def _get_task_prefix(self) -> str:
        """获取当前线程的任务前缀"""
        thread_id = threading.get_ident()
        if thread_id in self._task_contexts:
            ctx = self._task_contexts[thread_id]
            return f"[{ctx['task_name']}]"
        return ""
    
    def log(self, *args, level: str = "INFO", sep: str = " ", end: str = "\n", **kwargs):
        """
        线程安全的日志输出
        
        Args:
            *args: 要打印的内容
            level: 日志级别
            sep: 分隔符
            end: 结束符
        """
        with self._lock:
            timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            thread_name = threading.current_thread().name
            task_prefix = self._get_task_prefix()
            
            # 构建前缀
            prefix_parts = [f"[{timestamp}]", f"[{thread_name}]"]
            if task_prefix:
                prefix_parts.append(task_prefix)
            prefix = " ".join(prefix_parts) + " "
            
            # 输出日志
            message = sep.join(str(arg) for arg in args)
            print(prefix + message, end=end, **kwargs)
            sys.stdout.flush()
    
    def info(self, *args, **kwargs):
        """INFO级别日志"""
        self.log(*args, level="INFO", **kwargs)
    
    def warning(self, *args, **kwargs):
        """WARNING级别日志"""
        self.log(*args, level="WARNING", **kwargs)
    
    def error(self, *args, **kwargs):
        """ERROR级别日志"""
        self.log(*args, level="ERROR", **kwargs)
    
    def debug(self, *args, **kwargs):
        """DEBUG级别日志"""
        self.log(*args, level="DEBUG", **kwargs)
    
    def print_section(self, title: str, width: int = 80):
        """
        打印分隔线和标题
        
        Args:
            title: 标题
            width: 宽度
        """
        with self._lock:
            print("=" * width)
            if title:
                print(title.center(width))
                print("=" * width)
            sys.stdout.flush()
    
    def print_multiline(self, *lines: str):
        """
        打印多行内容（保证不被其他线程打断）
        
        Args:
            *lines: 多行文本
        """
        with self._lock:
            timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            thread_name = threading.current_thread().name
            task_prefix = self._get_task_prefix()
            
            prefix_parts = [f"[{timestamp}]", f"[{thread_name}]"]
            if task_prefix:
                prefix_parts.append(task_prefix)
            prefix = " ".join(prefix_parts) + " "
            
            for line in lines:
                print(prefix + line)
            sys.stdout.flush()


# 全局单例
_logger: Optional[ThreadSafeLogger] = None


def get_logger() -> ThreadSafeLogger:
    """获取线程安全日志记录器单例"""
    global _logger
    if _logger is None:
        _logger = ThreadSafeLogger()
    return _logger


# 便捷函数
def log(*args, **kwargs):
    """便捷的日志输出函数"""
    get_logger().log(*args, **kwargs)


def log_info(*args, **kwargs):
    """INFO级别日志"""
    get_logger().info(*args, **kwargs)


def log_warning(*args, **kwargs):
    """WARNING级别日志"""
    get_logger().warning(*args, **kwargs)


def log_error(*args, **kwargs):
    """ERROR级别日志"""
    get_logger().error(*args, **kwargs)


def log_debug(*args, **kwargs):
    """DEBUG级别日志"""
    get_logger().debug(*args, **kwargs)


def set_task_context(task_id: str, task_name: str):
    """为当前线程设置任务上下文"""
    get_logger().set_task_context(task_id, task_name)


def clear_task_context():
    """清除当前线程的任务上下文"""
    get_logger().clear_task_context()
