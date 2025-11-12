"""
高性能文件搜索器
使用ripgrep作为主引擎，Python作为fallback
"""

import os
import re
import subprocess
import json
from typing import List, Tuple, Optional, Set, Dict
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
import shutil

DEFAULT_IGNORE_DIRS = {
    "node_modules", "__pycache__", ".git", ".venv", "venv", "env",
    "dist", "build", "out", ".next", ".nuxt", "target",
    "vendor", ".pytest_cache", ".mypy_cache", "coverage"
}

class FastFileSearcher:
    """
    高性能文件搜索工具
    优先使用ripgrep，其次Python实现
    """
    
    def __init__(self):
        self.max_results = 300
        self.context_lines = 2
        self.max_file_size = 1_000_000  # 1MB
        self.min_file_size = 1
        self.ripgrep_available = self._check_ripgrep()
        self.file_cache = {}  # 文件内容缓存
        self.cache_max_size = 100  # 最多缓存100个文件
        
    def _check_ripgrep(self) -> bool:
        """检查ripgrep是否可用"""
        try:
            rg_path = shutil.which('rg')
            if not rg_path:
                print("[搜索引擎] ⚠ ripgrep 未找到，使用Python模式")
                return False
            
            result = subprocess.run(
                [rg_path, '--version'],
                capture_output=True,
                timeout=2,
                text=True
            )
            
            if result.returncode == 0:
                version = result.stdout.split('\n')[0]
                print(f"[搜索引擎] ✓ ripgrep 可用 ({version})")
                return True
            else:
                print("[搜索引擎] ⚠ ripgrep 检测失败，使用Python模式")
                return False
                
        except Exception as e:
            print(f"[搜索引擎] ⚠ ripgrep 检测异常: {e}，使用Python模式")
            return False
    
    def search(
        self, 
        directory: str, 
        regex: str, 
        file_pattern: str = "*"
    ) -> Dict[str, List[Dict]]:
        """
        搜索文件
        
        Args:
            directory: 搜索目录
            regex: 正则表达式
            file_pattern: 文件模式（支持多个，用逗号分隔）
            
        Returns:
            {文件路径: [匹配项列表]}
        """
        if self.ripgrep_available:
            return self._search_with_ripgrep(directory, regex, file_pattern)
        else:
            return self._search_with_python(directory, regex, file_pattern)
    
    def batch_search(
        self,
        directory: str,
        patterns: List[Tuple[str, str]]  # [(regex, file_pattern), ...]
    ) -> Dict[str, Dict[str, List[Dict]]]:
        """
        批量搜索多个模式（并发）
        
        Args:
            directory: 搜索目录
            patterns: [(regex, file_pattern), ...] 列表
            
        Returns:
            {pattern_key: {文件路径: [匹配项列表]}}
        """
        results = {}
        
        if self.ripgrep_available:
            # 使用并发ripgrep搜索
            with ThreadPoolExecutor(max_workers=min(4, len(patterns))) as executor:
                future_to_pattern = {
                    executor.submit(
                        self._search_with_ripgrep, 
                        directory, 
                        regex, 
                        file_pattern
                    ): (regex, file_pattern)
                    for regex, file_pattern in patterns
                }
                
                for future in as_completed(future_to_pattern):
                    regex, file_pattern = future_to_pattern[future]
                    pattern_key = f"{regex}|{file_pattern}"
                    try:
                        results[pattern_key] = future.result()
                    except Exception as e:
                        print(f"[批量搜索] ⚠️ 搜索出错 ({pattern_key}): {e}")
                        results[pattern_key] = {}
        else:
            # Python模式：顺序搜索（避免并发读取文件冲突）
            for regex, file_pattern in patterns:
                pattern_key = f"{regex}|{file_pattern}"
                try:
                    results[pattern_key] = self._search_with_python(
                        directory, regex, file_pattern
                    )
                except Exception as e:
                    print(f"[批量搜索] ⚠️ 搜索出错 ({pattern_key}): {e}")
                    results[pattern_key] = {}
        
        return results
    
    def _search_with_ripgrep(
        self,
        directory: str,
        regex: str,
        file_pattern: str = "*"
    ) -> Dict[str, List[Dict]]:
        """使用ripgrep进行搜索（高性能）"""
        try:
            # 构建ripgrep命令
            cmd = [
                'rg',
                '--json',  # JSON输出
                '-e', regex,  # 搜索模式
                '--context', str(self.context_lines),  # 上下文行数
                '--max-count', str(self.max_results),  # 最大结果数
                '--max-filesize', '1M',  # 最大文件大小
            ]
            
            # 添加文件模式过滤
            if file_pattern and file_pattern != "*":
                # 支持多个文件模式（逗号分隔）
                for pattern in file_pattern.split(','):
                    cmd.extend(['--glob', pattern.strip()])
            
            # 添加忽略目录
            for ignore_dir in DEFAULT_IGNORE_DIRS:
                cmd.extend(['--glob', f'!{ignore_dir}/**'])
            
            cmd.append(directory)
            
            # 执行搜索
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300  # 300秒超时
            )
            
            # ripgrep返回码：0=找到，1=未找到，2=错误
            if result.returncode in [0, 1]:
                return self._parse_ripgrep_json(result.stdout, directory)
            else:
                print(f"[ripgrep] ⚠️ 搜索失败: {result.stderr}")
                return {}
                
        except subprocess.TimeoutExpired:
            print(f"[ripgrep] ⚠️ 搜索超时，切换到Python模式")
            return self._search_with_python(directory, regex, file_pattern)
        except Exception as e:
            print(f"[ripgrep] ⚠️ 搜索错误: {e}，切换到Python模式")
            return self._search_with_python(directory, regex, file_pattern)
    
    def _parse_ripgrep_json(
        self,
        output: str,
        base_dir: str
    ) -> Dict[str, List[Dict]]:
        """解析ripgrep的JSON输出"""
        results = {}
        current_file = None
        current_matches = []
        
        for line in output.strip().split('\n'):
            if not line:
                continue
            
            try:
                data = json.loads(line)
                msg_type = data.get('type')
                
                if msg_type == 'begin':
                    # 新文件开始
                    path_data = data.get('data', {}).get('path', {})
                    file_path = path_data.get('text', '')
                    if file_path:
                        current_file = os.path.relpath(file_path, base_dir)
                        current_matches = []
                
                elif msg_type == 'match':
                    # 匹配行
                    if current_file:
                        match_data = data['data']
                        line_num = match_data.get('line_number', 0)
                        line_text = match_data.get('lines', {}).get('text', '').rstrip()
                        
                        # 收集上下文
                        current_matches.append({
                            'line_number': line_num,
                            'line': line_text,
                            'text': line_text,
                            'before': [],
                            'after': []
                        })
                
                elif msg_type == 'context':
                    # 上下文行
                    if current_file and current_matches:
                        context_data = data['data']
                        context_line_num = context_data.get('line_number', 0)
                        context_text = context_data.get('lines', {}).get('text', '').rstrip()
                        
                        last_match = current_matches[-1]
                        if context_line_num < last_match['line_number']:
                            last_match['before'].append(context_text)
                        else:
                            last_match['after'].append(context_text)
                
                elif msg_type == 'end':
                    # 文件结束
                    if current_file and current_matches:
                        results[current_file] = current_matches
                        current_file = None
                        current_matches = []
                        
            except json.JSONDecodeError:
                continue
        
        # 处理最后一个文件
        if current_file and current_matches:
            results[current_file] = current_matches
        
        return results
    
    def _search_with_python(
        self,
        directory: str,
        regex: str,
        file_pattern: str = "*"
    ) -> Dict[str, List[Dict]]:
        """使用Python进行搜索（备选方案）"""
        pattern = re.compile(regex)
        results = {}
        count = 0
        
        # 解析文件模式
        file_patterns = [p.strip() for p in file_pattern.split(',')]
        
        for root, dirs, files in os.walk(directory):
            # 过滤忽略目录
            dirs[:] = [d for d in dirs if d not in DEFAULT_IGNORE_DIRS]
            
            for file in files:
                if count >= self.max_results:
                    break
                
                # 文件模式匹配
                if file_pattern != "*":
                    import fnmatch
                    if not any(fnmatch.fnmatch(file, pat) for pat in file_patterns):
                        continue
                
                filepath = os.path.join(root, file)
                rel_path = os.path.relpath(filepath, directory)
                
                # 性能优化：跳过过大或过小的文件
                try:
                    file_size = os.path.getsize(filepath)
                    if file_size > self.max_file_size or file_size < self.min_file_size:
                        continue
                except OSError:
                    continue
                
                # 使用缓存读取文件
                lines = self._get_file_content(filepath)
                if lines is None:
                    continue
                
                for i, line in enumerate(lines):
                    if pattern.search(line):
                        count += 1
                        if rel_path not in results:
                            results[rel_path] = []
                        
                        before = lines[max(0, i-self.context_lines):i]
                        after = lines[i+1:i+1+self.context_lines]
                        
                        results[rel_path].append({
                            'line_number': i + 1,
                            'line': line.rstrip(),
                            'text': line.rstrip(),
                            'before': [l.rstrip() for l in before],
                            'after': [l.rstrip() for l in after]
                        })
                        
                        if count >= self.max_results:
                            break
        
        return results
    
    def _get_file_content(self, filepath: str) -> Optional[List[str]]:
        """获取文件内容（带缓存）"""
        # 检查缓存
        if filepath in self.file_cache:
            return self.file_cache[filepath]
        
        # 读取文件
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            # 缓存管理：先进先出
            if len(self.file_cache) >= self.cache_max_size:
                # 移除最早的缓存
                first_key = next(iter(self.file_cache))
                del self.file_cache[first_key]
            
            self.file_cache[filepath] = lines
            return lines
            
        except (UnicodeDecodeError, FileNotFoundError, PermissionError):
            return None
    
    def clear_cache(self):
        """清除文件缓存"""
        self.file_cache.clear()
        print("[搜索引擎] 🗑️ 缓存已清除")


# 向后兼容：保持与原FileSearcher相同的接口
class FileSearcher(FastFileSearcher):
    """兼容性别名"""
    pass
