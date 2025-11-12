import os
import re
from typing import List, Tuple, Optional, Set, Dict

DEFAULT_IGNORE_DIRS = {
    "node_modules", "__pycache__", ".git", ".venv", "venv", "env",
    "dist", "build", "out", ".next", ".nuxt", "target",
    "vendor", ".pytest_cache", ".mypy_cache", "coverage"
}

class FileSearcher:
    """文件搜索工具"""
    
    def __init__(self):
        self.max_results = 300
        self.context_lines = 2
        self.max_file_size = 1_000_000  # 1MB - 跳过超大文件
        self.min_file_size = 1  # 1字节 - 跳过空文件
    
    def search(self, directory: str, regex: str, file_pattern: str = "*") -> Dict[str, List[Dict]]:
        """搜索文件"""
        pattern = re.compile(regex)
        results = {}
        count = 0
        
        for root, dirs, files in os.walk(directory):
            # 过滤忽略目录
            dirs[:] = [d for d in dirs if d not in DEFAULT_IGNORE_DIRS]
            
            for file in files:
                if count >= self.max_results:
                    break
                
                # 文件模式匹配
                if file_pattern != "*":
                    import fnmatch
                    if not fnmatch.fnmatch(file, file_pattern):
                        continue
                
                filepath = os.path.join(root, file)
                rel_path = os.path.relpath(filepath, directory)
                
                # 性能优化：跳过过大或过小的文件
                try:
                    file_size = os.path.getsize(filepath)
                    if file_size > self.max_file_size:
                        continue  # 跳过超大文件（如二进制、生成文件）
                    if file_size < self.min_file_size:
                        continue  # 跳过空文件
                except OSError:
                    continue
                
                try:
                    with open(filepath, 'r', encoding='utf-8') as f:
                        lines = f.readlines()
                except (UnicodeDecodeError, FileNotFoundError, PermissionError):
                    continue
                
                for i, line in enumerate(lines):
                    if pattern.search(line):
                        count += 1
                        if rel_path not in results:
                            results[rel_path] = []
                        
                        before = lines[max(0, i-self.context_lines):i]
                        after = lines[i+1:i+1+self.context_lines]
                        
                        results[rel_path].append({
                            'line_number': i + 1,  # 修改字段名以匹配
                            'line': line.rstrip(),
                            'text': line.rstrip(),  # 保留兼容性
                            'before': [l.rstrip() for l in before],
                            'after': [l.rstrip() for l in after]
                        })
                        
                        if count >= self.max_results:
                            break
        
        return results
