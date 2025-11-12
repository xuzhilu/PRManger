"""
辅助函数
"""

from typing import List, Dict, Tuple
from .config import CONFIG


# 从配置加载PR规模分类阈值
def _get_pr_size_thresholds() -> Dict:
    """从配置文件读取PR规模阈值"""
    return CONFIG.get('pr_review', {}).get('size_thresholds', {
        'small': {'files': 5, 'lines': 200, 'diff_size': 10000},
        'medium': {'files': 20, 'lines': 1000, 'diff_size': 50000},
        'large': {'files': 50, 'lines': 3000, 'diff_size': 150000}
    })


def calculate_pr_size(pr_diff: str, pr_files: List[Dict]) -> Tuple[str, Dict]:
    """计算PR规模（从配置读取阈值）"""
    files_count = len(pr_files) if isinstance(pr_files, list) else 0
    diff_size = len(pr_diff) if pr_diff else 0
    
    lines_added = 0
    lines_deleted = 0
    if pr_diff:
        for line in pr_diff.split('\n'):
            if line.startswith('+') and not line.startswith('+++'):
                lines_added += 1
            elif line.startswith('-') and not line.startswith('---'):
                lines_deleted += 1
    
    lines_changed = lines_added + lines_deleted
    
    # 从配置读取阈值
    pr_size_thresholds = _get_pr_size_thresholds()
    
    pr_size = 'xlarge'
    
    for size_name in ['small', 'medium', 'large']:
        thresholds = pr_size_thresholds.get(size_name, {})
        if (files_count <= thresholds.get('files', 0) and 
            lines_changed <= thresholds.get('lines', 0) and 
            diff_size <= thresholds.get('diff_size', 0)):
            pr_size = size_name
            break
    
    pr_stats = {
        'files_count': files_count,
        'additions': lines_added,
        'deletions': lines_deleted,
        'lines_changed': lines_changed,
        'diff_size': diff_size
    }
    
    print(f"[规模评估] 规模: {pr_size.upper()}")
    print(f"[规模评估] 文件数: {files_count}, 修改行数: {lines_changed}, Diff大小: {diff_size} bytes")
    
    return pr_size, pr_stats
