"""
上下文收集智能体节点
根据代码分析智能体的需求收集代码上下文信息
使用 Ripgrep搜索 + AST代码块提取
"""

from src.core.state import PRReviewState
from src.utils.config import CONFIG
from src.analyzers.project_analyzer.fast_file_searcher import FastFileSearcher
import os
import json
import re
from typing import Dict, List, Any, Optional

# 导入AST解析器
try:
    from src.analyzers.project_analyzer.ast_parser import ASTParser
    AST_PARSER_AVAILABLE = True
except ImportError:
    AST_PARSER_AVAILABLE = False
    ASTParser = None


async def context_collector_node(state: PRReviewState) -> PRReviewState:
    """上下文收集智能体
    
    职责：
    1. 接收代码分析智能体的搜索请求
    2. 在代码仓库中搜索指定的函数/类/变量
    3. 提取使用这些项的代码片段
    4. 返回上下文信息给代码分析智能体
    """
    print("\n" + "="*60)
    print("=== 上下文收集智能体 ===")
    print("="*60)
    
    # 获取请求
    context_request = state.get("context_request")
    pr_size = state.get("pr_size", "medium")
    iteration_count = state.get("iteration_count", 0)
    all_collected_context = state.get("all_collected_context", {})
    
    if not context_request:
        print("[上下文收集] ⚠️ 无收集请求，跳过")
        return {"current_stage": "code_analysis"}
    
    search_items = context_request.get('search_items', [])
    analysis_note = context_request.get('analysis_note', '')
    
    print(f"[上下文收集] 📊 迭代 {iteration_count}")
    print(f"[上下文收集] 🔍 搜索项: {len(search_items)} 个")
    if analysis_note:
        print(f"[上下文收集] 💡 分析说明: {analysis_note}")
    
    # 初始化工具
    repo_path = CONFIG['git_repo']['repo_path']
    file_searcher = FastFileSearcher()
    
    # 获取AST缓存
    ast_cache = state.get("ast_cache", {})
    
    # 显示功能状态
    print(f"[上下文收集] 🛠️ 功能状态:")
    print(f"[上下文收集]   - Ripgrep搜索引擎: ✓")
    print(f"[上下文收集]   - AST代码块提取: {'✓' if AST_PARSER_AVAILABLE else '✗ (降级为固定行数)'}")
    
    # 根据规模调整参数
    size_config = {
        'small': {'max_files_per_item': 30, 'max_matches_per_file': 6},
        'medium': {'max_files_per_item': 20, 'max_matches_per_file': 4},
        'large': {'max_files_per_item': 16, 'max_matches_per_file': 4},
        'xlarge': {'max_files_per_item': 10, 'max_matches_per_file': 2}
    }
    
    config = size_config.get(pr_size, size_config['medium'])
    
    # 收集每个搜索项的使用情况
    dependencies = {}
    
    # 第一阶段：分离已缓存和需要搜索的项
    items_to_search = []
    for item in search_items:
        item_name = item.get('name', '')
        item_type = item.get('type', 'unknown')
        
        if not item_name:
            print(f"[上下文收集] ⚠️ 跳过空名称的搜索项")
            continue
        
        # 检查是否已收集过
        if item_name in all_collected_context:
            print(f"[上下文收集] ℹ️ {item_type}: {item_name} - 已缓存，复用")
            dependencies[item_name] = all_collected_context[item_name]
        else:
            items_to_search.append(item)
    
    # 第二阶段：使用 Ripgrep + AST 搜索
    updated_ast_cache = ast_cache
    if items_to_search:
        print(f"[上下文收集] 🚀 批量搜索 {len(items_to_search)} 个新项...")
        dependencies, updated_ast_cache = _ripgrep_ast_search(
            items_to_search,
            file_searcher,
            config,
            repo_path,
            dependencies,
            ast_cache
        )
    
    # 合并到累积上下文
    updated_all_context = {**all_collected_context, **dependencies}
    
    print(f"\n[上下文收集] ✅ 收集完成")
    print(f"[上下文收集]   - 本轮收集: {len(dependencies)} 个项")
    print(f"[上下文收集]   - 累积总数: {len(updated_all_context)} 个")
    print(f"[上下文收集]   - AST缓存: {len(updated_ast_cache)} 个文件")
    print(f"[上下文收集] 🔄 返回代码分析智能体...")
    print("="*60 + "\n")
    
    return {
        "context_response": {
            "dependencies": dependencies,
            "summary": f"收集了{len(dependencies)}个项的使用信息",
            "iteration": iteration_count
        },
        "all_collected_context": updated_all_context,
        "context_request": None,
        "current_stage": "code_analysis",
        "ast_cache": updated_ast_cache
    }


def _build_search_patterns(name: str, item_type: str) -> List[str]:
    """根据类型构建搜索模式 - 支持多种编程语言"""
    patterns = []
    
    # 转义特殊字符
    escaped_name = re.escape(name)
    
    if item_type == 'function':
        # 函数调用模式（Python/C++/C#/Java）
        patterns.extend([
            rf'\b{escaped_name}\s*\(',  # 函数调用
            rf'from\s+\S+\s+import\s+.*\b{escaped_name}\b',  # Python import
            rf'import\s+.*\b{escaped_name}\b',  # import
        ])
    elif item_type == 'class':
        # 类使用模式（Python/C++/C#/Java）
        patterns.extend([
            rf'\b{escaped_name}\b',  # 通用匹配（最重要）
            rf'\b{escaped_name}\s*\(',  # 实例化
            rf'\b{escaped_name}\s*\*',  # C++指针
            rf'\b{escaped_name}\s*&',  # C++引用
            rf':\s*(?:public|private|protected)?\s*{escaped_name}\b',  # C++继承
            rf':\s*{escaped_name}\b',  # 类型注解/继承
            rf'class\s+\w+\s*:\s*.*{escaped_name}',  # C#/Java继承
            rf'new\s+{escaped_name}\b',  # new关键字
            rf'from\s+\S+\s+import\s+.*\b{escaped_name}\b',  # import
            rf'isinstance\s*\([^,]+,\s*{escaped_name}\b',  # isinstance检查
        ])
    elif item_type == 'variable':
        # 变量使用模式 - 包括字符串字面量（用于注册表键等）
        patterns.extend([
            rf'\b{escaped_name}\b',  # 直接使用
            rf'["\'].*{escaped_name}.*["\']',  # 字符串中（大小写敏感）
            rf'L?".*{escaped_name}.*"',  # C++ wide string
            rf'@".*{escaped_name}.*"',  # C# verbatim string
            rf'from\s+\S+\s+import\s+.*\b{escaped_name}\b',  # import
        ])
        
        # 对于配置项，添加不同大小写变体
        # 例如: notifyOnSuccess -> NotifyOnSuccess, NOTIFY_ON_SUCCESS
        if name[0].islower():  # 如果是小驼峰
            # 转换为大驼峰 (Pascal Case)
            pascal_case = name[0].upper() + name[1:]
            patterns.append(rf'\b{re.escape(pascal_case)}\b')
            patterns.append(rf'["\'].*{re.escape(pascal_case)}.*["\']')
            
            # 转换为大写下划线 (SCREAMING_SNAKE_CASE)
            import re as re_module
            snake_case = re_module.sub(r'([A-Z])', r'_\1', name).upper()
            if snake_case != name.upper():
                patterns.append(rf'\b{re.escape(snake_case)}\b')
    else:
        # 通用模式
        patterns.append(rf'\b{escaped_name}\b')
    
    return patterns


def _simplify_matches(matches_dict: Dict[str, List[Dict]]) -> Dict[str, List[str]]:
    """简化匹配结果，只保留关键信息"""
    simplified = {}
    for file_path, matches in matches_dict.items():
        simplified[file_path] = [
            f"L{m.get('line_number', '?')}: {str(m.get('line', ''))[:50]}"
            for m in matches[:2]  # 每个文件最多2个示例
        ]
    return simplified


def _extract_code_context(repo_path: str, matches_dict: Dict[str, List[Dict]], ast_cache: Dict[str, List]) -> tuple:
    """
    提取代码上下文片段用于深度分析
    优先使用AST精确提取完整代码块，fallback到固定行数
    
    Returns:
        (code_snippets, updated_ast_cache)
    """
    code_snippets = []
    updated_cache = dict(ast_cache) 
    
    # 初始化AST解析器（如果可用）
    ast_parser = None
    if AST_PARSER_AVAILABLE:
        try:
            ast_parser = ASTParser()
            if ast_parser.available:
                print("[上下文收集]   🌲 使用AST精确提取代码块 + 缓存")
            else:
                ast_parser = None
        except Exception as e:
            print(f"[上下文收集]   ⚠️ AST解析器初始化失败: {e}")
            ast_parser = None
    
    for file_path, matches in list(matches_dict.items())[:3]:  # 最多3个文件
        try:
            full_path = os.path.join(repo_path, file_path)
            if not os.path.exists(full_path):
                continue
                
            with open(full_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            # 检查AST缓存或解析新文件
            ast_nodes = []
            if ast_parser:
                if file_path in updated_cache:
                    # 从缓存读取
                    ast_nodes = updated_cache[file_path]
                    print(f"[上下文收集]   📦 {file_path}: 从AST缓存读取")
                else:
                    # 解析新文件并加入缓存
                    try:
                        ast_nodes = ast_parser.parse_file(full_path)
                        if ast_nodes:
                            updated_cache[file_path] = ast_nodes
                            print(f"[上下文收集]   🌲 {file_path}: 解析AST并缓存 ({len(ast_nodes)} 个定义)")
                    except Exception as e:
                        print(f"[上下文收集]   ⚠️ AST解析失败 {file_path}: {e}")
            
            for match in matches[:5]:  # 每文件最多2个匹配
                line_num = match.get('line_number', 0)
                if line_num <= 0:
                    continue
                
                # 尝试使用AST定位代码块
                if ast_nodes:
                    snippet = _extract_ast_code_block(
                        file_path, 
                        line_num, 
                        lines, 
                        ast_nodes, 
                        match
                    )
                    if snippet:
                        code_snippets.append(snippet)
                        continue
                
                # Fallback: 固定行数提取
                snippet = _extract_fixed_lines_context(
                    file_path,
                    line_num,
                    lines,
                    match
                )
                if snippet:
                    code_snippets.append(snippet)
                    
        except Exception as e:
            print(f"[上下文收集]   ⚠️ 提取代码上下文出错: {e}")
            continue
    
    return code_snippets, updated_cache


def _extract_ast_code_block(
    file_path: str,
    line_num: int,
    lines: List[str],
    ast_nodes: List,
    match: Dict
) -> Optional[Dict]:
    """
    使用AST精确提取包含指定行的完整代码块
    
    Args:
        file_path: 文件路径
        line_num: 匹配的行号
        lines: 文件所有行
        ast_nodes: AST节点列表
        match: 原始匹配信息
        
    Returns:
        代码片段字典，包含完整的函数/类定义
    """
    try:
        # 查找包含该行的最小AST节点（函数或类）
        enclosing_node = None
        for node in ast_nodes:
            if node.line_number <= line_num <= node.end_line:
                # 找到包含该行的节点，优先选择最小的（最具体的）
                if enclosing_node is None or (
                    node.end_line - node.line_number < 
                    enclosing_node.end_line - enclosing_node.line_number
                ):
                    enclosing_node = node
        
        if not enclosing_node:
            # 没找到包含该行的节点，返回None以fallback
            return None
        
        # 提取完整代码块
        start_line = enclosing_node.line_number - 1  # 转为0-based索引
        end_line = enclosing_node.end_line  # end_line已经是包含的
        
        # 确保不越界
        start_line = max(0, start_line)
        end_line = min(len(lines), end_line)
        
        code_block = ''.join(lines[start_line:end_line])
        
        # 计算代码块大小（限制过大的块）
        block_lines = end_line - start_line
        if block_lines > 300:
            # 如果代码块太大，只提取关键部分
            # 取匹配行前后各50行，但不超过代码块范围
            context_start = max(start_line, line_num - 51)
            context_end = min(end_line, line_num + 50)
            code_block = ''.join(lines[context_start:context_end])
            block_info = f"[代码块过大，显示部分: L{context_start+1}-L{context_end}]"
        else:
            block_info = f"[完整{enclosing_node.type}定义: L{start_line+1}-L{end_line}]"
        
        # 构建返回信息
        return {
            "file": file_path,
            "line": line_num,
            "function": enclosing_node.name,
            "type": enclosing_node.type,
            "start_line": start_line + 1,
            "end_line": end_line,
            "context": code_block,
            "matched_line": lines[line_num - 1] if line_num <= len(lines) else "",
            "extraction_method": "AST",
            "block_info": block_info,
            "docstring": enclosing_node.docstring if hasattr(enclosing_node, 'docstring') else None,
            "params": enclosing_node.params if hasattr(enclosing_node, 'params') else None
        }
        
    except Exception as e:
        print(f"[上下文收集]   ⚠️ AST代码块提取失败: {e}")
        return None


def _extract_fixed_lines_context(
    file_path: str,
    line_num: int,
    lines: List[str],
    match: Dict
) -> Optional[Dict]:
    """
    固定行数提取（Fallback方法）
    提取匹配行前后各5行
    """
    try:
        # 提取上下文：前后各5行
        start = max(0, line_num - 11)
        end = min(len(lines), line_num + 10)
        context_lines = lines[start:end]
        
        # 查找所属函数/类（使用正则）
        function_name = _find_enclosing_function(lines, line_num)
        
        return {
            "file": file_path,
            "line": line_num,
            "function": function_name,
            "type": "unknown",
            "start_line": start + 1,
            "end_line": end,
            "context": ''.join(context_lines),
            "matched_line": lines[line_num - 1] if line_num <= len(lines) else "",
            "extraction_method": "fixed_lines",
            "block_info": f"[前后5行上下文: L{start+1}-L{end}]"
        }
        
    except Exception as e:
        print(f"[上下文收集]   ⚠️ 固定行数提取失败: {e}")
        return None


def _find_enclosing_function(lines: List[str], line_num: int) -> str:
    """查找包含指定行的函数/类名"""
    import re
    
    # 向上查找函数或类定义
    for i in range(line_num - 1, max(0, line_num - 50), -1):
        line = lines[i]
        
        # 匹配函数定义
        func_match = re.match(r'\s*(async\s+)?def\s+(\w+)', line)
        if func_match:
            return func_match.group(2)
        
        # 匹配类定义
        class_match = re.match(r'\s*class\s+(\w+)', line)
        if class_match:
            return class_match.group(1)
    
    return "未知函数"


def _ripgrep_ast_search(
    items_to_search: List[Dict],
    file_searcher: FastFileSearcher,
    config: Dict,
    repo_path: str,
    dependencies: Dict,
    ast_cache: Dict[str, List]
) -> tuple:
    """
    使用 Ripgrep + AST 进行代码搜索和上下文提取
    
    流程：
    1. Ripgrep快速定位符号使用位置
    2. AST精确提取完整代码块并缓存
    3. 返回结构化的上下文信息
    
    Returns:
        (dependencies, updated_ast_cache)
    """
    print("[上下文收集] 🔎 Ripgrep搜索 + AST代码块提取")
    updated_ast_cache = ast_cache
    
    # 构建批量搜索模式
    batch_patterns = []
    item_pattern_map = {}
    file_pattern = "*.py,*.cpp,*.h,*.hpp,*.c,*.cs,*.java,*.js,*.ts"
    
    for item in items_to_search:
        item_name = item.get('name', '')
        item_type = item.get('type', 'unknown')
        
        print(f"[上下文收集] 🔍 {item_type}: {item_name}")
        if item.get('reason'):
            print(f"[上下文收集]   原因: {item['reason']}")
        
        patterns = _build_search_patterns(item_name, item_type)
        
        for pattern in patterns:
            pattern_key = f"{pattern}|{file_pattern}"
            batch_patterns.append((pattern, file_pattern))
            if pattern_key not in item_pattern_map:
                item_pattern_map[pattern_key] = []
            item_pattern_map[pattern_key].append(item)
    
    # 执行批量搜索
    batch_results = file_searcher.batch_search(repo_path, batch_patterns)
    
    # 整理搜索结果
    for item in items_to_search:
        item_name = item.get('name', '')
        item_type = item.get('type', 'unknown')
        item_reason = item.get('reason', '')
        
        # 合并该项相关的所有搜索结果
        all_results = {}
        patterns = _build_search_patterns(item_name, item_type)
        
        for pattern in patterns:
            pattern_key = f"{pattern}|{file_pattern}"
            if pattern_key in batch_results:
                results = batch_results[pattern_key]
                for file_path, matches in results.items():
                    if file_path in all_results:
                        all_results[file_path].extend(matches)
                    else:
                        all_results[file_path] = matches
        
        # 去重和限制结果
        limited_results = {}
        for file_path, matches in list(all_results.items())[:config['max_files_per_item']]:
            unique_matches = []
            seen_lines = set()
            for match in matches:
                line_num = match.get('line_number')
                if line_num and line_num not in seen_lines:
                    seen_lines.add(line_num)
                    unique_matches.append(match)
            
            limited_results[file_path] = unique_matches[:config['max_matches_per_file']]
        
        usage_count = sum(len(matches) for matches in limited_results.values())
        
        # 提取代码上下文片段（使用AST + 缓存）
        code_snippets, updated_ast_cache = _extract_code_context(repo_path, limited_results, updated_ast_cache)
        
        dependencies[item_name] = {
            "type": item_type,
            "reason": item_reason,
            "used_in_files": list(limited_results.keys()),
            "usage_count": usage_count,
            "usage_details": _simplify_matches(limited_results),
            "code_snippets": code_snippets,
            "search_status": "未找到使用" if usage_count == 0 else f"找到{usage_count}处使用"
        }
        
        print(f"[上下文收集]   ✓ {dependencies[item_name]['search_status']}")
        if code_snippets:
            ast_count = sum(1 for s in code_snippets if s.get('extraction_method') == 'AST')
            if ast_count > 0:
                print(f"[上下文收集]   🌲 AST提取: {ast_count}/{len(code_snippets)} 个代码块")
    
    return dependencies, updated_ast_cache
