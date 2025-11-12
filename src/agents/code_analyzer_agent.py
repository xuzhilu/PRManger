"""
代码分析智能体节点
直接分析diff代码块，判断修改对工程的影响
支持迭代式上下文收集
"""

from langgraph.config import get_stream_writer
from src.core.state import PRReviewState
from langchain_core.messages import SystemMessage, HumanMessage
from src.utils.llm import llm, parser
from src.analyzers.project_analyzer import ASTParser, AST_AVAILABLE
from src.utils.config import CONFIG
import json
import os
from typing import Dict, List, Any


async def code_analyzer_node(state: PRReviewState) -> PRReviewState:
    """代码分析智能体
    
    职责：
    1. 使用大模型分析diff代码块
    2. 判断修改是否会对工程产生影响
    3. 如需上下文，向上下文收集智能体请求
    4. 根据diff和上下文继续分析
    5. 直到得出明确结论或达到迭代上限
    """
    print("\n" + "="*60)
    print("=== 代码分析智能体 ===")
    print("="*60)
    writer = get_stream_writer()
    
    # 获取基本信息
    pr_diff = state.get("pr_diff", "")
    changed_files = state.get("changed_files", [])
    pr_size = state.get("pr_size", "medium")
    
    # 迭代式分析相关状态
    iteration_count = state.get("iteration_count", 0)
    impact_chain = state.get("impact_chain", [])
    
    # AST解析：只在首轮解析changed_files
    repo_path = CONFIG['git_repo']['repo_path']
    ast_cache = state.get("ast_cache", {})
    updated_ast_cache = ast_cache
    
    if iteration_count == 0:
        # 首轮：解析changed_files的AST并存入缓存-----ast提示词，ast结构
        ast_context, updated_ast_cache = _extract_ast_context(changed_files, repo_path, pr_size, ast_cache)
    else:
        # 后续轮：直接使用缓存中的AST
        ast_context = _generate_ast_context_from_cache(changed_files, ast_cache, pr_size)
    
    # 检查上下文响应
    context_response = state.get("context_response")
    
    print(f"[代码分析] 📊 当前状态: 迭代 {iteration_count + 1}")
    
    # 从配置获取深度分析参数
    deep_analysis_config = CONFIG.get('pr_review', {}).get('deep_analysis', {})
    max_iterations = deep_analysis_config.get('max_iterations', 6)
    
    # 根据规模智能调整参数
    size_config = {
        'small': {
            'max_iterations': max_iterations,
            'diff_chars': 6000,
            'max_defs_per_request': 10,
            'max_files': 10,
            'context_summary_chars': 9000
        },
        'medium': {
            'max_iterations': max_iterations,
            'diff_chars': 4500,
            'max_defs_per_request': 5,
            'max_files': 5,
            'context_summary_chars': 6000
        },
        'large': {
            'max_iterations': max_iterations - 2 if max_iterations > 2 else 2,
            'diff_chars': 3000,
            'max_defs_per_request': 3,
            'max_files': 3,
            'context_summary_chars': 4500
        },
        'xlarge': {
            'max_iterations': max_iterations - 4 if max_iterations > 4 else 2,
            'diff_chars': 2400,
            'max_defs_per_request': 2,
            'max_files': 2,
            'context_summary_chars': 3000
        }
    }
    
    config = size_config.get(pr_size, size_config['medium'])
    max_iterations = config['max_iterations']
    
    # 检查是否达到最大迭代次数
    if iteration_count >= max_iterations:
        print(f"[代码分析] ⚠️ 达到最大迭代次数 ({max_iterations})，强制完成分析")
        return {
            "analysis_conclusion": {
                "has_critical_issues": False,
                "critical_issues": [],
                "potential_risks": [f"达到最大迭代深度({max_iterations})，可能存在未发现的深层影响"],
                "summary": f"完成 {iteration_count} 轮迭代分析",
                "iteration_info": {
                    "total_iterations": iteration_count,
                    "impact_chain_depth": len(impact_chain)
                }
            },
            "current_stage": "analysis_complete",
            "iteration_count": iteration_count
        }
    
    # 构建system prompt - 直接分析diff代码块
    system_prompt = """你是代码影响分析专家。你的任务是分析diff代码块，判断修改是否会对工程产生影响。

                    ## 分析流程
                    1. **首轮**：直接分析diff代码块
                    - 理解代码修改的具体内容（新增/修改/删除）
                    - 判断这些修改是否可能影响其他模块
                    - 如需要了解某个函数/类的使用情况，请求上下文收集

                    2. **后续轮**：结合上下文继续分析
                    - 分析收集到的代码片段，理解实际调用关系
                    - 判断这些调用是否会受到影响
                    - 继续追踪影响链请求上下文收集，或给出最终结论

                    ## 分析重点
                    - 删除的函数/类是否还有其他地方在使用
                    - 修改的接口是否会影响调用方
                    - 新增的代码逻辑是否正确
                    - 完整的影响路径：修改A → 影响B → 导致C失效

                    ## 输出格式（纯JSON）

                    ### 请求更多上下文
                    当你发现某个被删除/修改的函数/类/变量，需要知道谁在使用它时：
                    ```json
                    {
                    "action": "request_context",
                    "params": {
                        "search_items": [
                        {
                            "name": "函数名或类名",
                            "type": "function|class|variable",
                            "reason": "需要了解使用情况的原因"
                        }
                        ],
                        "analysis_note": "当前分析到的情况说明"
                    }
                    }
                    ```

                    ### 给出最终结论
                    当你已经完成分析，有明确结论时：
                    ```json
                    {
                    "action": "conclusion",
                    "result": {
                        "has_critical_issues": true|false,
                        "critical_issues": [
                        "具体的确定性问题描述"
                        ],
                        "impact_chains": [
                        "影响链：A → B → C"
                        ],
                        "affected_features": ["受影响的功能模块"],
                        "summary": "总体分析结论"
                    }
                    }
                    ```

                    ## 重要原则
                    1. 基于实际代码给出确定性结论
                    2. 如果diff中删除/修改了某个定义，需要知道谁在使用它
                    3. 追踪完整的影响链
                    4. 宁愿多搜索，不要遗漏
                    5. 只输出JSON，不要额外解释"""
    
    # 构建分析提示
    if iteration_count == 0:
        # 首轮分析 - 直接分析diff + AST结构
        initial_prompt = f"""
## 首轮分析 - 代码Diff分析 + 语法树结构

**修改规模**: {pr_size.upper()}
**修改文件**: {len(changed_files)} 个

{ast_context}

**Diff代码块**:
```diff
{pr_diff}
```

**分析任务**:
1. **结合语法树理解代码结构** - 上面的语法树展示了完整的函数、类、方法定义
2. 仔细阅读diff，理解具体修改内容
3. 识别删除的函数/类/变量（用-标记的行，对照语法树确认）
4. 识别修改的函数/类（既有-又有+的部分，查看语法树中的参数和类型）
5. 判断这些修改是否会影响其他代码

**注意**:
- 语法树提供了精确的代码结构，包括函数参数、返回类型、文档字符串
- 重点关注删除操作（-开头的行）
- 如果删除了函数/类定义，需要知道是否有其他地方在调用
- 给出基于代码的确定性判断
"""
    else:
        # 后续迭代 - 包含上下文信息
        code_snippets_text = ""
        if context_response and 'dependencies' in context_response:
            code_snippets_text = "\n## 收集到的上下文代码\n\n"
            for item_name, dep_info in context_response['dependencies'].items():
                usage_count = dep_info.get('usage_count', 0)
                code_snippets_text += f"### {item_name} 的使用情况（共{usage_count}处）:\n\n"
                
                snippets = dep_info.get('code_snippets', [])
                if snippets:
                    for snippet in snippets[:]:
                        code_snippets_text += f"**文件**: {snippet['file']}\n"
                        code_snippets_text += f"**函数**: {snippet['function']}\n"
                        code_snippets_text += f"**行号**: {snippet['line']}\n"
                        code_snippets_text += f"```\n{snippet['context']}\n```\n\n"
                else:
                    code_snippets_text += f"  未找到使用该项的代码\n\n"
        
        # 构建影响链摘要
        chain_summary = ""
        if impact_chain:
            chain_summary = "\n## 当前影响链追踪\n\n"
            for entry in impact_chain:
                chain_summary += f"**迭代{entry['iteration']}**: {entry.get('analysis_note', '分析中...')}\n"
        
        initial_prompt = f"""
## 第 {iteration_count + 1} 轮迭代分析

{chain_summary}

{code_snippets_text}

**Diff代码（回顾）**:
```diff
{pr_diff}
```

**分析任务**:
1. 结合上面收集到的代码上下文
2. 判断diff中的修改是否会影响这些调用点
3. 如果这些调用点还可能影响其它函数/类的使用就继续收集上下文
3. 如果还需要了解更多函数/类的使用情况，继续请求
4. 如果已经明确影响范围，给出最终结论

**要求**:
- 基于实际代码给出明确结论
- 说明完整的影响路径
- 避免使用"可能"等模糊词汇
"""
    
    conversation = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=initial_prompt)
    ]
    
    # 调用LLM
    import hashlib
    call_signature = hashlib.md5(f"{iteration_count}_{len(pr_diff)}".encode()).hexdigest()[:8]
    print(f"[代码分析] 🤖 调用LLM (迭代 {iteration_count + 1}, ID:{call_signature})...")
    
    def validate_schema(data: dict) -> bool:
        if "action" not in data:
            return False
        action = data.get("action")
        if action == "request_context":
            return "params" in data and "search_items" in data["params"]
        elif action == "conclusion":
            return "result" in data
        return False
    
    # 从配置读取LLM重试和超时参数
    llm_retry_config = CONFIG.get('llm', {}).get('retry', {})
    llm_timeout_config = CONFIG.get('llm', {}).get('timeout', {})
    max_retries = llm_retry_config.get('code_analyzer', 3)
    timeout = llm_timeout_config.get('code_analyzer', 3600)
    
    result = await parser.parse_json_with_retry(
        conversation=conversation,
        expected_schema={"action": str},
        custom_validator=validate_schema,
        max_retries=max_retries,
        parser_name="code_analyzer_diff",
        timeout=timeout
    )
    
    if not result:
        print(f"[代码分析] ❌ LLM解析失败")
        return {
            "analysis_conclusion": {
                "has_critical_issues": False,
                "critical_issues": [],
                "potential_risks": ["LLM响应解析失败"],
                "summary": f"第{iteration_count + 1}轮迭代解析失败"
            },
            "current_stage": "analysis_complete",
            "iteration_count": iteration_count + 1
        }
    
    action = result.get("action")
    
    if action == "conclusion":
        # 分析完成
        conclusion = result.get("result", {})
        print(f"[代码分析] ✅ 分析完成 (共{iteration_count + 1}轮)")
        print(f"[代码分析]   - 是否有问题: {conclusion.get('has_critical_issues', False)}")
        print("="*60 + "\n")
        
        # 添加迭代信息
        conclusion["iteration_info"] = {
            "total_iterations": iteration_count + 1,
            "impact_chain_depth": len(impact_chain)
        }
        
        return {
            "analysis_conclusion": conclusion,
            "current_stage": "analysis_complete",
            "iteration_count": iteration_count + 1,
            "impact_chain": impact_chain,
            "ast_cache": updated_ast_cache
        }
    
    elif action == "request_context":
        # 需要更多上下文
        params = result.get("params", {})
        search_items = params.get("search_items", [])
        analysis_note = params.get("analysis_note", "")
        
        if not search_items:
            # 没有搜索项，强制结论
            print(f"[代码分析] ⚠️ 未指定搜索项，强制完成")
            return {
                "analysis_conclusion": {
                    "has_critical_issues": False,
                    "critical_issues": [],
                    "potential_risks": ["未指定需要搜索的项"],
                    "summary": f"完成{iteration_count + 1}轮迭代"
                },
                "current_stage": "analysis_complete",
                "iteration_count": iteration_count + 1
            }
        
        # 提取搜索项名称
        search_names = [item.get("name", "") for item in search_items if item.get("name")]
        
        if not search_names:
            print(f"[代码分析] ⚠️ 搜索项缺少名称，强制完成")
            return {
                "analysis_conclusion": {
                    "has_critical_issues": False,
                    "critical_issues": [],
                    "potential_risks": ["搜索项格式错误"],
                    "summary": f"完成{iteration_count + 1}轮迭代"
                },
                "current_stage": "analysis_complete",
                "iteration_count": iteration_count + 1
            }
        
        print(f"[代码分析] 📋 请求搜索: {', '.join(search_names[:])}...")
        print(f"[代码分析] 💡 分析说明: {analysis_note}")
        print(f"[代码分析] 🔄 转交上下文收集智能体...")
        print("="*60 + "\n")
        
        # 记录影响链
        new_chain_entry = {
            "iteration": iteration_count + 1,
            "search_items": search_items,
            "analysis_note": analysis_note
        }
        updated_chain = impact_chain + [new_chain_entry]
        
        return {
            "context_request": {
                "search_items": search_items,
                "analysis_note": analysis_note
            },
            "current_stage": "context_collection",
            "iteration_count": iteration_count + 1,
            "impact_chain": updated_chain
        }
    
    else:
        print(f"[代码分析] ⚠️ 未知action: {action}")
        return {
            "analysis_conclusion": {
                "has_critical_issues": False,
                "critical_issues": [],
                "potential_risks": [f"未知action: {action}"],
                "summary": "分析异常"
            },
            "current_stage": "analysis_complete",
            "iteration_count": iteration_count + 1
        }


def _extract_ast_context(changed_files: List[str], repo_path: str, pr_size: str, ast_cache: Dict[str, List]) -> tuple:
    """
    提取变更文件的AST语法树结构并缓存
    为LLM提供精确的代码结构信息
    
    Returns:
        (ast_context_str, updated_ast_cache)
    """
    if not AST_AVAILABLE:
        return "\n**注**: AST解析器未安装，使用基础分析模式\n", ast_cache
    
    parser = ASTParser()
    ast_info = []
    updated_cache = dict(ast_cache)
    
    print(f"[AST解析] 🌳 首轮解析 {len(changed_files)} 个变更文件...")
    
    for file_path in changed_files[:]:
        full_path = os.path.join(repo_path, file_path)
        
        if not os.path.exists(full_path):
            continue
        
        try:
            # 解析AST
            nodes = parser.parse_file(full_path)
            
            if nodes:
                # 存入缓存
                updated_cache[file_path] = nodes
                
                # 生成LLM友好的语法树摘要
                ast_summary = parser.generate_llm_context(nodes, include_docstring=True)
                
                ast_info.append(f"""
                                    ### 📄 {file_path} - 代码结构（语法树）

                                    {ast_summary}
                                    """)
                
                print(f"[AST解析]   ✓ {file_path}: {len(nodes)} 个定义 → 已缓存")
        
        except Exception as e:
            print(f"[AST解析]   ⚠️ {file_path}: 解析失败 - {e}")
            continue
    
    if ast_info:
        header = f"""
                    ## 🌳 代码语法树结构（AST解析）

                    以下是变更文件的精确代码结构，包含所有函数、类、方法的定义：
                    - **函数参数**: 明确列出，方便理解接口
                    - **返回类型**: 显示类型信息（如有）
                    - **文档字符串**: 理解函数用途
                    - **所属关系**: 方法属于哪个类

                    这些信息帮助你准确理解代码修改的影响范围。

                    {''.join(ast_info)}

                    ---
                    """
        print(f"[AST解析] ✅ 首轮语法树解析完成，共 {len(ast_info)} 个文件已缓存")
        return header, updated_cache
    else:
        return "\n**注**: 无法解析语法树，将仅基于diff文本分析\n", updated_cache


def _generate_ast_context_from_cache(changed_files: List[str], ast_cache: Dict[str, List], pr_size: str) -> str:
    """
    从缓存中生成AST上下文（后续轮使用）
    
    Args:
        changed_files: 变更文件列表
        ast_cache: AST缓存
        pr_size: PR规模
        
    Returns:
        AST上下文字符串
    """
    if not AST_AVAILABLE or not ast_cache:
        return "\n**注**: AST缓存为空\n"
    
    # 根据PR规模限制文件数
    size_limits = {
        'small': 10,
        'medium': 6,
        'large': 4,
        'xlarge': 2
    }
    max_files = size_limits.get(pr_size, 3)
    
    parser = ASTParser()
    ast_info = []
    cached_count = 0
    
    print(f"[AST解析] 📦 从缓存读取语法树...")
    
    for file_path in changed_files[:max_files]:
        if file_path in ast_cache:
            nodes = ast_cache[file_path]
            
            ast_summary = parser.generate_llm_context(nodes, include_docstring=True)
            
            ast_info.append(f"""
                            ### 📄 {file_path} - 代码结构（语法树）

                            {ast_summary}
                            """)
            cached_count += 1
            print(f"[AST解析]   ✓ {file_path}: 从缓存读取")
    
    if ast_info:
        header = f"""
                    ## 🌳 代码语法树结构（从缓存读取）

                    以下是变更文件的精确代码结构，包含所有函数、类、方法的定义：

                    {''.join(ast_info)}

                    ---
                    """
        print(f"[AST解析] ✅ 从缓存读取 {cached_count} 个文件的语法树")
        return header
    else:
        return "\n**注**: 缓存中无相关AST信息\n"
