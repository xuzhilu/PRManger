"""
PR拆分智能体
"""

import re
import json
from typing import List, Dict, Set, Tuple
from langchain_core.messages import SystemMessage, HumanMessage
from langgraph.config import get_stream_writer
from src.core.state import PRReviewState
from src.utils.helpers import calculate_pr_size
from src.utils.config import CONFIG


async def pr_splitter_node(state: PRReviewState) -> PRReviewState:
    """PR拆分智能体节点
    
    职责：
    1. 从Git获取分支diff内容
    2. 评估PR规模
    3. 判断是否需要拆分
    4. 如果需要拆分，将PR按模块拆分为多个子PR
    """
    print("\n" + "="*60)
    print("=== PR拆分智能体 ===")
    print("="*60)
    writer = get_stream_writer()
    writer({"stage": "pr_splitter", "status": "started"})
    
    source_branch = state.get("source_branch")
    target_branch = state.get("target_branch")
    
    if not all([source_branch, target_branch]):
        print("[错误] 分支信息不完整")
        return {
            "current_stage": "splitter_failed",
            "feedback_message": "分支信息不完整"
        }
    
    # 使用全局git_adapter
    from src.adapters.git_adapter import get_git_adapter
    git_adapter = get_git_adapter()
    
    # 1. 获取分支diff信息
    print(f"[步骤1] 获取分支diff: {source_branch} → {target_branch}")
    try:
        branch_info = await git_adapter.get_branch_diff(source_branch, target_branch)
        writer({"branch_info_fetched": True})
        print("[步骤1] ✓ 成功获取diff信息")
    except Exception as e:
        print(f"[步骤1] ✗ 获取失败: {e}")
        writer({"error": str(e)})
        return {
            "current_stage": "splitter_failed",
            "feedback_message": f"获取分支信息失败：{str(e)}"
        }
    
    pr_diff = branch_info.get("diff", "")
    branch_content = branch_info.get("content", {})
    pr_files = branch_info.get("files", [])
    
    # 2. 评估PR规模
    print("[步骤2] 评估PR规模...")
    pr_size, pr_stats = calculate_pr_size(pr_diff, pr_files)
    
    print(f"[步骤2] ✓ PR规模: {pr_size.upper()}")
    print(f"        文件数: {pr_stats.get('files_count', 0)}")
    print(f"        代码行: +{pr_stats.get('additions', 0)} -{pr_stats.get('deletions', 0)}")
    
    writer({"pr_size_evaluation": {
        "pr_size": pr_size,
        "stats": pr_stats
    }})
    
    # 3. 判断是否需要拆分
    print("\n[步骤3] 判断是否需要拆分...")
    needs_split = _should_split_pr(pr_size, pr_stats, pr_files)
    
    if not needs_split:
        print("[步骤3] ✓ PR规模适中，无需拆分")
        print("[步骤3] 下一步: 单个PR处理（子图）")
        print("="*60 + "\n")
        
        return {
            "pr_diff": pr_diff,
            "pr_files": pr_files,
            "pr_size": pr_size,
            "pr_stats": pr_stats,
            "needs_split": False,
            "is_sub_pr": False,
            "current_stage": "single_pr_review"  # 新阶段：直接进入单PR处理子图
        }
    
    # 4. 执行拆分
    print("[步骤3] ⚠️ PR规模较大，需要拆分")
    print("\n[步骤4] 执行智能拆分...")
    
    sub_prs = await _split_pr_by_modules(pr_diff, pr_files, pr_stats)
    
    if not sub_prs or len(sub_prs) <= 1:
        print("[步骤4] ⚠️ 拆分失败或拆分后仍为单个PR，使用原始PR")
        return {
            "pr_diff": pr_diff,
            "pr_files": pr_files,
            "pr_size": pr_size,
            "pr_stats": pr_stats,
            "needs_split": False,
            "is_sub_pr": False,
            "current_stage": "single_pr_review"  # 降级为单PR处理
        }
    
    print(f"[步骤4] ✓ 成功拆分为 {len(sub_prs)} 个子PR")
    for i, sub_pr in enumerate(sub_prs, 1):
        print(f"        子PR {i}: {sub_pr.get('title', f'SubPR-{i}')} ({len(sub_pr.get('files', []))} 个文件)")
    
    print("\n[拆分完成] 下一步: 批量处理各子PR（循环子图）")
    print("="*60 + "\n")
    
    writer({"split_result": {
        "sub_prs_count": len(sub_prs),
        "sub_prs": [{"title": sp.get("title"), "files_count": len(sp.get("files", []))} for sp in sub_prs]
    }})
    
    return {
        "pr_diff": pr_diff,
        "pr_files": pr_files,
        "pr_size": pr_size,
        "pr_stats": pr_stats,
        "needs_split": True,
        "sub_prs": sub_prs,
        "sub_pr_results": [],
        "is_sub_pr": False,
        "parent_pr_id": f"{source_branch}_{target_branch}",
        "current_stage": "sub_pr_review"  # 新阶段：进入批量子PR处理（循环子图）
    }


def _should_split_pr(pr_size: str, pr_stats: Dict, pr_files: List) -> bool:
    """判断PR是否需要拆分
    
    拆分依据：diff字节数是否超过配置阈值
    """
    # 从配置读取拆分阈值
    splitting_config = CONFIG.get('pr_review', {}).get('splitting', {})
    thresholds = splitting_config.get('thresholds', {})
    
    # 使用diff_size作为唯一拆分标准
    diff_size = pr_stats.get('diff_size', 0)
    diff_size_threshold = thresholds.get('diff_size', 50000)
    
    if diff_size > diff_size_threshold:
        
        return True
    return False


async def _split_pr_by_modules(pr_diff: str, pr_files: List[Dict], pr_stats: Dict) -> List[Dict]:
    """使用依赖关系感知的智能拆分
    
    策略：
    0. 分析文件间依赖关系，构建依赖组
    1. 按目录分组，但保持依赖组完整性
    2. 如果规则拆分失败，降级为按文件数量均分
    """
    print("[拆分策略] 使用依赖关系感知的智能拆分...")
    
    # 从配置读取设置
    splitting_config = CONFIG.get('pr_review', {}).get('splitting', {})
    target_diff_size = splitting_config.get('target_diff_size', 50000)
    enable_dependency_analysis = splitting_config.get('enable_dependency_analysis', True)
    
    # 提取文件路径信息
    file_paths = []
    for file_info in pr_files:
        if isinstance(file_info, dict):
            file_path = file_info.get('path', file_info.get('filename', ''))
        else:
            file_path = str(file_info)
        if file_path:
            file_paths.append(file_path)
    
    # 按文件拆分diff
    file_diffs = _split_diff_by_file(pr_diff)
    
    # 步骤0: 分析依赖关系
    dependency_groups = []
    if enable_dependency_analysis:
        print("[依赖分析] 🔍 分析文件间依赖关系...")
        dependency_groups = _analyze_and_group_dependencies(file_paths, file_diffs)
        
        if dependency_groups:
            print(f"[依赖分析] ✓ 发现 {len(dependency_groups)} 个依赖组")
            for i, group in enumerate(dependency_groups, 1):
                group_size = sum(len(file_diffs.get(f, '').encode('utf-8')) for f in group)
                print(f"        - 依赖组 {i}: {len(group)} 个文件, {group_size} bytes")
                for f in group[:3]:
                    print(f"          • {f}")
                if len(group) > 3:
                    print(f"          ... 还有 {len(group)-3} 个文件")
            
            # 找出独立文件
            files_in_groups = set()
            for group in dependency_groups:
                files_in_groups.update(group)
            
            independent_files = [f for f in file_paths if f not in files_in_groups]
            if independent_files:
                for f in independent_files:
                    dependency_groups.append([f])
                print(f"[依赖分析] ℹ️ 另有 {len(independent_files)} 个独立文件（无依赖关系）")
        else:
            dependency_groups = [[f] for f in file_paths]
            print("[依赖分析] ℹ️ 未发现文件间依赖，所有文件独立")
    else:
        dependency_groups = [[f] for f in file_paths]
        print("[依赖分析] ⚠️ 依赖分析已禁用，所有文件独立处理")
    
    # 策略1: 优先按依赖组拆分（保证依赖完整性）
    if len(dependency_groups) > 1:
        print(f"[拆分策略] ✓ 发现多个依赖组，直接按依赖组拆分（共 {len(dependency_groups)} 组）")
        return _split_by_dependency_groups(dependency_groups, file_diffs)
    
    # 策略2: 按目录分组（保持依赖组完整）
    print("[拆分策略] 只有1个依赖组，尝试按目录分组...")
    module_groups = _group_dependency_aware_by_directory(dependency_groups, file_diffs, target_diff_size)
    
    if len(module_groups) > 1:
        print(f"[拆分策略] ✓ 按目录分组成功，共 {len(module_groups)} 个模块")
        sub_prs = []
        for module_name, files_info in module_groups.items():
            sub_pr = {
                "title": f"[子PR] {module_name}",
                "files": files_info['files'],
                "diff": files_info['diff'],
                "module": module_name
            }
            sub_prs.append(sub_pr)
        return sub_prs
    
    # 策略3: 简单均分（每个子PR最多5个文件，避免破坏依赖）
    print("[拆分策略] 目录分组失败，使用简单均分策略（保持依赖组完整）...")
    chunk_size = 5
    sub_prs = []
    
    for i in range(0, len(file_paths), chunk_size):
        chunk_files = file_paths[i:i+chunk_size]
        chunk_diff = "\n".join([file_diffs.get(f, "") for f in chunk_files])
        
        sub_pr = {
            "title": f"[子PR] 文件组 {i//chunk_size + 1}",
            "files": [{"path": f} for f in chunk_files],
            "diff": chunk_diff,
            "module": f"group_{i//chunk_size + 1}"
        }
        sub_prs.append(sub_pr)
    
    print(f"[拆分策略] ✓ 均分完成，共 {len(sub_prs)} 个子PR")
    return sub_prs

def _split_diff_by_file(pr_diff: str) -> Dict:
    """将diff按文件分割"""
    file_diffs = {}
    current_file = None
    current_content = []
    
    for line in pr_diff.split('\n'):
        if line.startswith('diff --git'):
            if current_file:
                file_diffs[current_file] = '\n'.join(current_content)
            # 提取文件名
            match = re.search(r'b/(.+)$', line)
            current_file = match.group(1) if match else 'unknown'
            current_content = [line]
        elif current_file:
            current_content.append(line)
    
    if current_file:
        file_diffs[current_file] = '\n'.join(current_content)
    
    return file_diffs


def _analyze_and_group_dependencies(file_paths: List[str], file_diffs: Dict) -> List[List[str]]:
    """分析文件间依赖关系并构建依赖组
    
    使用并查集（Union-Find）将有依赖的文件分组
    """
    # 步骤1: 提取每个文件中变更的定义
    file_definitions = {}
    for file_path in file_paths:
        diff = file_diffs.get(file_path, '')
        definitions = _extract_changed_definitions_from_diff(diff)
        if definitions:
            file_definitions[file_path] = definitions
    
    if not file_definitions:
        return []
    
    # 步骤2: 构建依赖图
    dependencies = {}
    for file_path in file_paths:
        diff = file_diffs.get(file_path, '')
        deps = set()
        
        for other_file, defs in file_definitions.items():
            if other_file == file_path:
                continue
            
            for def_name, def_type in defs:
                if _has_reference_in_diff(diff, def_name, def_type):
                    deps.add(other_file)
                    print(f"        [依赖] {file_path} → {other_file} (使用了 {def_type}: {def_name})")
        
        if deps:
            dependencies[file_path] = list(deps)
    
    # 步骤3: 使用并查集构建依赖组
    parent = {f: f for f in file_paths}
    
    def find(x):
        if parent[x] != x:
            parent[x] = find(parent[x])
        return parent[x]
    
    def union(x, y):
        px, py = find(x), find(y)
        if px != py:
            parent[px] = py
    
    for file_a, dep_files in dependencies.items():
        for file_b in dep_files:
            union(file_a, file_b)
    
    groups_dict = {}
    for file_path in file_paths:
        root = find(file_path)
        if root not in groups_dict:
            groups_dict[root] = []
        groups_dict[root].append(file_path)
    
    dependency_groups = [group for group in groups_dict.values() if len(group) > 1]
    return dependency_groups


def _extract_changed_definitions_from_diff(diff: str) -> List[Tuple[str, str]]:
    """从diff中提取被修改或新增的定义"""
    definitions = []
    
    func_patterns = [
        r'^[-+]\s*(?:virtual\s+)?(?:static\s+)?(\w+)\s+(\w+)\s*\([^)]*\)',
        r'^[-+]\s*def\s+(\w+)\s*\(',
        r'^[-+]\s*function\s+(\w+)\s*\(',
    ]
    
    class_patterns = [
        r'^[-+]\s*class\s+(\w+)',
        r'^[-+]\s*struct\s+(\w+)',
    ]
    
    for line in diff.split('\n'):
        for pattern in func_patterns:
            match = re.search(pattern, line)
            if match:
                func_name = match.group(2) if len(match.groups()) > 1 else match.group(1)
                if func_name not in ['if', 'while', 'for', 'switch', 'catch', 'return']:
                    definitions.append((func_name, 'function'))
                    break
        
        for pattern in class_patterns:
            match = re.search(pattern, line)
            if match:
                definitions.append((match.group(1), 'class'))
                break
    
    return list(set(definitions))


def _has_reference_in_diff(diff: str, name: str, def_type: str) -> bool:
    """检查diff中是否引用了指定的定义"""
    escaped_name = re.escape(name)
    added_lines = [line for line in diff.split('\n') if line.startswith('+') and not line.startswith('+++')]
    
    if def_type == 'function':
        patterns = [
            rf'\b{escaped_name}\s*\(',
            rf'::{escaped_name}\s*\(',
            rf'\b{escaped_name}\b',
        ]
    elif def_type == 'class':
        patterns = [
            rf'\b{escaped_name}\b',
            rf'\b{escaped_name}\s*\(',
            rf'\b{escaped_name}\s*[*&]',
        ]
    else:
        patterns = [rf'\b{escaped_name}\b']
    
    for line in added_lines:
        for pattern in patterns:
            if re.search(pattern, line):
                return True
    return False


def _split_by_dependency_groups(dependency_groups: List[List[str]], file_diffs: Dict) -> List[Dict]:
    """直接按依赖组拆分为子PR
    
    策略：
    1. 每个依赖组（len>1）作为独立子PR
    2. 独立文件：如果总代码量不高，合并为一个子PR；否则分开
    """
    from src.utils.config import CONFIG
    
    sub_prs = []
    dep_group_count = 0
    
    # 分离依赖组和独立文件
    dependency_groups_only = []
    independent_files = []
    
    for group in dependency_groups:
        if len(group) > 1:
            dependency_groups_only.append(group)
        else:
            independent_files.extend(group)
    
    # 处理依赖组 - 每个作为独立子PR
    for group in dependency_groups_only:
        dep_group_count += 1
        group_diff = "\n".join([file_diffs.get(f, "") for f in group])
        group_size = len(group_diff.encode('utf-8'))
        
        sub_pr = {
            "title": f"[子PR] 依赖组 {dep_group_count} ({len(group)}个相互依赖的文件)",
            "files": [{"path": f} for f in group],
            "diff": group_diff,
            "diff_size": group_size,
            "is_dependency_group": True
        }
        sub_prs.append(sub_pr)
    
    # 处理独立文件
    if independent_files:
        # 计算独立文件总代码量
        total_independent_size = sum(
            len(file_diffs.get(f, "").encode('utf-8')) for f in independent_files
        )
        
        # 使用target_diff_size作为每组目标大小
        splitting_config = CONFIG.get('pr_review', {}).get('splitting', {})
        target_size = splitting_config.get('target_diff_size', 50000)
        
        if total_independent_size <= target_size:
            # 代码量不高，合并为一个子PR
            combined_diff = "\n".join([file_diffs.get(f, "") for f in independent_files])
            sub_pr = {
                "title": f"[子PR] 独立文件组 ({len(independent_files)}个独立文件)",
                "files": [{"path": f} for f in independent_files],
                "diff": combined_diff,
                "diff_size": total_independent_size,
                "is_dependency_group": False
            }
            sub_prs.append(sub_pr)
            print(f"        [合并] {len(independent_files)}个独立文件合并为1个子PR (总计 {total_independent_size} bytes)")
        else:
            # 代码量较大，使用贪心算法分组（每组尽量接近target_size）
            grouped_files = _group_independent_files_by_size(independent_files, file_diffs, target_size)
            
            for i, group_files in enumerate(grouped_files, 1):
                group_diff = "\n".join([file_diffs.get(f, "") for f in group_files])
                group_size = len(group_diff.encode('utf-8'))
                
                if len(group_files) == 1:
                    title = f"[子PR] 独立文件 {i}"
                else:
                    title = f"[子PR] 独立文件组 {i} ({len(group_files)}个独立文件)"
                
                sub_pr = {
                    "title": title,
                    "files": [{"path": f} for f in group_files],
                    "diff": group_diff,
                    "diff_size": group_size,
                    "is_dependency_group": False
                }
                sub_prs.append(sub_pr)
            
            print(f"        [智能分组] {len(independent_files)}个独立文件分为{len(grouped_files)}组 (总计 {total_independent_size} bytes)")
            for i, group_files in enumerate(grouped_files, 1):
                group_size = sum(len(file_diffs.get(f, "").encode('utf-8')) for f in group_files)
                print(f"            - 组{i}: {len(group_files)}个文件, {group_size} bytes")
    
    return sub_prs


def _group_independent_files_by_size(independent_files: List[str], file_diffs: Dict, target_size: int) -> List[List[str]]:
    """使用贪心算法将独立文件按大小分组
    
    策略：
    1. 按文件大小降序排列
    2. 使用First Fit Decreasing算法，将文件分配到尽量接近target_size的组中
    3. 尽量避免单文件一组（除非文件本身超过target_size）
    """
    # 计算每个文件的大小
    file_sizes = [(f, len(file_diffs.get(f, "").encode('utf-8'))) for f in independent_files]
    # 按大小降序排列
    file_sizes.sort(key=lambda x: x[1], reverse=True)
    
    groups = []
    
    for file_path, file_size in file_sizes:
        # 尝试找到一个合适的组加入（总大小不超过target_size）
        placed = False
        for group in groups:
            group_size = sum(len(file_diffs.get(f, "").encode('utf-8')) for f in group)
            if group_size + file_size <= target_size:
                group.append(file_path)
                placed = True
                break
        
        # 如果没有合适的组，创建新组
        if not placed:
            groups.append([file_path])
    
    return groups


def _group_dependency_aware_by_directory(dependency_groups: List[List[str]], file_diffs: Dict, target_size: int) -> Dict:
    """依赖关系感知的目录分组
    
    将依赖组按目录分组，但确保每个依赖组完整性
    """
    dir_groups = {}
    
    for dep_group in dependency_groups:
        # 找到这个依赖组的主要目录（出现最多的目录）
        dir_counts = {}
        for file_path in dep_group:
            parts = file_path.split('/')
            dir_name = parts[0] if len(parts) > 1 else "根目录"
            dir_counts[dir_name] = dir_counts.get(dir_name, 0) + 1
        
        # 选择文件最多的目录
        main_dir = max(dir_counts, key=dir_counts.get)
        
        if main_dir not in dir_groups:
            dir_groups[main_dir] = {
                'files': [],
                'diff': ""
            }
        
        # 将整个依赖组加入该目录
        for file_path in dep_group:
            dir_groups[main_dir]['files'].append({"path": file_path})
            if file_path in file_diffs:
                dir_groups[main_dir]['diff'] += file_diffs[file_path] + "\n"
    
    # 过滤掉太小的组
    filtered = {k: v for k, v in dir_groups.items() if len(v['files']) >= 2}
    
    return filtered if len(filtered) > 1 else dir_groups
