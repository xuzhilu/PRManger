import os
from typing import List, Tuple, Optional, Set, Dict
from collections import deque

DEFAULT_IGNORE_DIRS = {
    "node_modules", "__pycache__", ".git", ".venv", "venv", "env",
    "dist", "build", "out", ".next", ".nuxt", "target",
    "vendor", ".pytest_cache", ".mypy_cache", "coverage"
}

class FileEnumerator:
    """文件列举工具"""
    
    def __init__(self, ignore_dirs: Optional[Set[str]] = None):
        self.ignore_dirs = ignore_dirs or DEFAULT_IGNORE_DIRS
        
    def should_ignore(self, name: str) -> bool:
        """判断是否应该忽略该文件/目录"""
        if name in self.ignore_dirs:
            return True
        if name.startswith('.') and name not in {'.gitignore', '.env'}:
            return True
        return False
    
    def list_files_recursive(self, directory: str, limit: int = 200) -> Tuple[List[str], bool]:
        """递归列举文件（广度优先）"""
        results = []
        queue = deque([directory])
        visited = set()
        hit_limit = False
        
        while queue and len(results) < limit:
            current_dir = queue.popleft()
            
            if current_dir in visited:
                continue
            visited.add(current_dir)
            
            try:
                entries = sorted(os.listdir(current_dir))
            except (PermissionError, FileNotFoundError):
                continue
            
            for entry in entries:
                if len(results) >= limit:
                    hit_limit = True
                    break
                
                full_path = os.path.join(current_dir, entry)
                rel_path = os.path.relpath(full_path, directory)
                
                if self.should_ignore(entry):
                    continue
                
                if os.path.isdir(full_path):
                    results.append(rel_path + '/')
                    queue.append(full_path)
                else:
                    results.append(rel_path)
        
        return results, hit_limit