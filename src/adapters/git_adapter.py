"""
原生Git仓库适配器
功能：为自建Git仓库提供分支操作接口
适用场景：服务器上自建Git仓库 + SVN远程提交
"""

import os
import subprocess
from typing import Dict, List, Optional, Any
from datetime import datetime
from src.utils.config import CONFIG


class NativeGitAdapter:
    """
    原生Git仓库适配器
    
    使用场景：
    1. 服务器上有自建的Git仓库
    2. 提交者通过SVN或Git远程提交到服务器
    3. 系统部署在服务器上，直接访问Git仓库
    4. 完全基于分支进行代码审查
    """
    
    def __init__(self, repo_path: str, base_branch: str = "main", 
                 enable_diff_cache: bool = True):
        """
        初始化Git适配器
        
        Args:
            repo_path: 服务器上Git仓库的本地路径
            base_branch: 基础分支名称，默认为main
            enable_diff_cache: 是否启用diff缓存，默认True
        """
        self.repo_path = repo_path
        self.base_branch = base_branch
        self._diff_cache = {} if enable_diff_cache else None
        self._ensure_repo()
    
    def _ensure_repo(self):
        """确保仓库存在并有效"""
        if not os.path.exists(self.repo_path):
            raise ValueError(f"仓库路径不存在: {self.repo_path}")
        
        if not os.path.exists(os.path.join(self.repo_path, ".git")):
            raise ValueError(f"不是有效的Git仓库: {self.repo_path}")
        
        print(f"[INFO] Git仓库路径: {self.repo_path}")
    
    def _run_git_command(self, command: List[str], binary_mode: bool = False, timeout: int = 300) -> str:
        """
        执行Git命令
        
        Args:
            command: Git命令参数列表
            binary_mode: 是否使用二进制模式（用于可能包含二进制文件的diff）
            timeout: 命令超时时间（秒），默认300秒（5分钟）
            
        Returns:
            命令输出结果
        """
        import time
        start_time = time.time()
        
        try:
            if binary_mode:
                # 二进制模式，手动处理解码
                result = subprocess.run(
                    ["git"] + command,
                    cwd=self.repo_path,
                    capture_output=True,
                    check=True,
                    timeout=timeout
                )
                # 尝试用UTF-8解码，失败则用latin-1（不会抛出异常）
                try:
                    output = result.stdout.decode('utf-8')
                except UnicodeDecodeError:
                    # 使用errors='ignore'忽略无法解码的字节
                    output = result.stdout.decode('utf-8', errors='ignore')
                    print("[WARN] Git输出包含无法解码的字符，已忽略")
                
                elapsed = time.time() - start_time
                if elapsed > 10:  # 如果执行时间超过10秒，记录警告
                    print(f"[WARN] Git命令执行耗时 {elapsed:.2f}秒: {' '.join(command)}")
                
                return output.strip()
            else:
                # 文本模式
                result = subprocess.run(
                    ["git"] + command,
                    cwd=self.repo_path,
                    capture_output=True,
                    text=True,
                    check=True,
                    encoding='utf-8',
                    timeout=timeout
                )
                
                elapsed = time.time() - start_time
                if elapsed > 10:  # 如果执行时间超过10秒，记录警告
                    print(f"[WARN] Git命令执行耗时 {elapsed:.2f}秒: {' '.join(command)}")
                
                return result.stdout.strip()
        except subprocess.TimeoutExpired:
            elapsed = time.time() - start_time
            print(f"[ERROR] Git命令执行超时（{elapsed:.2f}秒）: {' '.join(command)}")
            raise Exception(f"Git命令执行超时（超过{timeout}秒）")
        except subprocess.CalledProcessError as e:
            elapsed = time.time() - start_time
            print(f"[ERROR] Git命令执行失败（耗时{elapsed:.2f}秒）: {' '.join(command)}")
            if hasattr(e, 'stderr'):
                if isinstance(e.stderr, bytes):
                    print(f"[ERROR] 错误信息: {e.stderr.decode('utf-8', errors='ignore')}")
                else:
                    print(f"[ERROR] 错误信息: {e.stderr}")
            raise
    
    async def get_branch_diff(self, source_branch: str, target_branch: str, 
                            use_cache: bool = True,
                            filter_large_files: bool = True,
                            large_file_threshold: int = 1024 * 1024) -> Dict[str, Any]:
        """
        获取两个分支之间的diff信息
        
        Args:
            source_branch: 源分支
            target_branch: 目标分支
            use_cache: 是否使用缓存，默认True
            filter_large_files: 是否过滤大文件，默认True
            large_file_threshold: 大文件阈值（字节），默认1MB
            
        Returns:
            包含diff、files等信息的字典
        """
        try:
            print(f"[INFO] 正在获取分支 {source_branch} -> {target_branch} 的diff...")
            
            # 检查分支是否存在
            try:
                self._run_git_command(["rev-parse", "--verify", source_branch])
                self._run_git_command(["rev-parse", "--verify", target_branch])
            except subprocess.CalledProcessError:
                raise ValueError(f"分支 {source_branch} 或 {target_branch} 不存在")
            
            # 获取源分支最新提交信息
            print(f"[INFO] 获取提交信息...")
            commit_hash = self._run_git_command(["rev-parse", source_branch])
            target_hash = self._run_git_command(["rev-parse", target_branch])
            
            # 检查缓存
            cache_key = f"{source_branch}_{target_branch}_{commit_hash}_{target_hash}"
            if use_cache and self._diff_cache is not None and cache_key in self._diff_cache:
                print(f"[INFO] 使用缓存的diff数据")
                return self._diff_cache[cache_key]
            
            # 先获取文件列表和统计信息（快速操作）
            print(f"[INFO] 获取文件变更统计...")
            
            # 一次性获取所有文件的统计信息
            numstat_output = self._run_git_command([
                "diff",
                "--numstat",
                f"{target_branch}...{source_branch}"
            ])
            
            # 获取文件状态
            files_output = self._run_git_command([
                "diff",
                "--name-status",
                f"{target_branch}...{source_branch}"
            ])
            
            # 解析统计信息
            stats_map = {}
            large_files = []
            total_changes = 0
            
            for line in numstat_output.split('\n'):
                if line.strip():
                    parts = line.split('\t')
                    if len(parts) >= 3:
                        additions = int(parts[0]) if parts[0] != '-' else 0
                        deletions = int(parts[1]) if parts[1] != '-' else 0
                        filename = parts[2]
                        changes = additions + deletions
                        total_changes += changes
                        stats_map[filename] = (additions, deletions)
                        
                        # 检测大文件（基于变更行数）
                        if filter_large_files and changes > large_file_threshold / 100:  # 假设每行约100字节
                            large_files.append(filename)
            
            if large_files:
                print(f"[WARN] 检测到 {len(large_files)} 个大文件变更: {large_files[:5]}...")
            
            # 构建文件列表
            files = []
            filtered_count = 0
            for line in files_output.split('\n'):
                if line.strip():
                    parts = line.split('\t')
                    if len(parts) >= 2:
                        status_code = parts[0]
                        filename = parts[1]
                        
                        # 过滤大文件（如果启用）
                        if filter_large_files and filename in large_files:
                            filtered_count += 1
                            continue
                        
                        status_map = {
                            'A': 'added',
                            'M': 'modified',
                            'D': 'deleted',
                            'R': 'renamed',
                            'C': 'copied'
                        }
                        status = status_map.get(status_code[0], 'modified')
                        
                        # 从统计映射中获取数据
                        additions, deletions = stats_map.get(filename, (0, 0))
                        
                        files.append({
                            "filename": filename,
                            "status": status,
                            "additions": additions,
                            "deletions": deletions,
                            "changes": additions + deletions
                        })
            
            if filtered_count > 0:
                print(f"[INFO] 过滤了 {filtered_count} 个大文件")
            print(f"[INFO] 完成！共 {len(files)} 个文件变更（总变更行数: {total_changes}）")
            
            # 只在需要时获取完整diff（大型项目优化关键）
            diff = None
            if total_changes < 10000:  # 少于1万行变更时才获取完整diff
                print(f"[INFO] 获取完整diff内容...")
                diff = self._run_git_command([
                    "diff",
                    f"{target_branch}...{source_branch}"
                ], binary_mode=True)
            else:
                print(f"[WARN] 变更过大（{total_changes}行），跳过完整diff获取以提升性能")
                diff = f"[变更过大，共{total_changes}行变更，已跳过完整diff]"
            
            result = {
                "diff": diff,
                "content": {
                    "source_branch": source_branch,
                    "target_branch": target_branch
                },
                "files": files,
                "stats": {
                    "total_files": len(files),
                    "total_changes": total_changes,
                    "filtered_files": filtered_count,
                    "large_files": len(large_files)
                }
            }
            
            # 缓存结果
            if use_cache and self._diff_cache is not None:
                self._diff_cache[cache_key] = result
                print(f"[INFO] 已缓存diff数据")
            
            return result
        except Exception as e:
            print(f"[ERROR] 获取分支diff失败: {str(e)}")
            raise
    
    async def get_branch_info(self, branch_name: str) -> Dict[str, Any]:
        """
        获取分支信息
        
        Args:
            branch_name: 分支名称
            
        Returns:
            分支信息字典
        """
        try:
            print(f"[INFO] 正在获取分支 {branch_name} 的信息...")
            
            # 检查分支是否存在
            try:
                self._run_git_command(["rev-parse", "--verify", branch_name])
            except subprocess.CalledProcessError:
                raise ValueError(f"分支 {branch_name} 不存在")
            
            # 获取分支最新提交
            commit_hash = self._run_git_command(["rev-parse", branch_name])
            commit_msg = self._run_git_command(["log", "-1", "--pretty=%B", commit_hash])
            commit_author = self._run_git_command(["log", "-1", "--pretty=%an", commit_hash])
            commit_email = self._run_git_command(["log", "-1", "--pretty=%ae", commit_hash])
            commit_date = self._run_git_command(["log", "-1", "--pretty=%ci", commit_hash])
            
            # 获取与基础分支的差异统计
            stats = self._run_git_command([
                "diff",
                "--shortstat",
                f"{self.base_branch}...{branch_name}"
            ])
            
            return {
                "title": commit_msg.split('\n')[0] if commit_msg else branch_name,
                "description": commit_msg,
                "author": commit_author,
                "author_email": commit_email,
                "created_at": commit_date,
                "updated_at": commit_date,
                "branch_name": branch_name,
                "base_branch": self.base_branch,
                "stats": stats
            }
        except Exception as e:
            print(f"[ERROR] 获取分支信息失败: {str(e)}")
            raise
    
    async def list_branches(self, pattern: Optional[str] = None) -> List[str]:
        """
        列出所有分支
        
        Args:
            pattern: 可选的分支名称模式（如 "feature/*"）
            
        Returns:
            分支名称列表
        """
        try:
            # 列出所有分支
            if pattern:
                print(f"[INFO] 正在列出匹配 {pattern} 的分支...")
                branches_output = self._run_git_command(["branch", "--list", pattern])
            else:
                print(f"[INFO] 正在列出所有分支...")
                branches_output = self._run_git_command(["branch", "--list"])
            
            branches = []
            for branch in branches_output.split('\n'):
                if branch.strip():
                    # 移除 * 标记和前导空格
                    branch_name = branch.strip().replace('*', '').strip()
                    branches.append(branch_name)
            
            print(f"[INFO] 找到 {len(branches)} 个分支")
            return branches
            
        except Exception as e:
            print(f"[ERROR] 列出分支失败: {str(e)}")
            raise
    
    def get_file_content(self, filepath: str, branch: Optional[str] = None) -> str:
        """
        获取指定文件的内容
        
        Args:
            filepath: 文件路径
            branch: 分支名（可选，默认当前分支）
            
        Returns:
            文件内容
        """
        try:
            if branch:
                content = self._run_git_command(["show", f"{branch}:{filepath}"])
            else:
                full_path = os.path.join(self.repo_path, filepath)
                with open(full_path, 'r', encoding='utf-8') as f:
                    content = f.read()
            
            return content
        except Exception as e:
            print(f"[ERROR] 获取文件内容失败 {filepath}: {str(e)}")
            return ""


# 创建全局实例（从配置文件加载）
_git_adapter_instance = None

def get_git_adapter() -> NativeGitAdapter:
    """获取全局Git适配器实例"""
    global _git_adapter_instance
    if _git_adapter_instance is None:
        _git_adapter_instance = NativeGitAdapter(
            repo_path=CONFIG['git_repo']['repo_path'],
            base_branch=CONFIG['git_repo']['base_branch']
        )
    return _git_adapter_instance
