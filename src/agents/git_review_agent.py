import re
import json
from langchain_core.messages import SystemMessage, HumanMessage
from langgraph.config import get_stream_writer
from src.core.state import PRReviewState
from src.utils.config import load_code_rules, CONFIG
from src.utils.llm import llm

async def git_review_node(state: PRReviewState) -> PRReviewState:
    """Git规范检查智能体
    
    职责：
    1. 使用大模型检测代码是否违反规范
    2. 将检查结果传递给代码分析智能体
    
    注意：Diff获取和规模评估已移至splitter_agent
    """
    print("\n" + "="*60)
    print("=== Git规范检查智能体 ===")
    print("="*60)
    writer = get_stream_writer()
    writer({"stage": "git_review", "status": "started"})
    
    # 从state中获取已经准备好的数据
    pr_diff = state.get("pr_diff", "")
    pr_files = state.get("pr_files", [])
    pr_size = state.get("pr_size", "unknown")
    pr_stats = state.get("pr_stats", {})
    
    if not pr_diff:
        print("[错误] 缺少PR diff信息")
        return {
            "current_stage": "git_review_failed",
            "feedback_message": "缺少PR diff信息"
        }
    
    # 1. 代码规范检查（使用大模型）
    print("\n[步骤1] 代码规范检查...")
    violations = []
    
    # 性能优化：对于大型PR，跳过LLM检查（从配置读取阈值）
    git_check_config = CONFIG.get('pr_review', {}).get('git_check', {})
    skip_llm_diff_size = git_check_config.get('skip_llm_diff_size', 50000)
    
    should_skip_llm = (
        pr_size in ['xlarge'] or 
        pr_stats.get('diff_size', 0) > skip_llm_diff_size
    )
    
    if should_skip_llm:
        print(f"[步骤1] ⚠️ PR规模较大（{pr_size}），跳过LLM规范检查")
        print(f"[步骤1] 使用快速规则检查代替")
        
        quick_violations = _quick_rule_check(pr_diff)
        if quick_violations:
            violations.extend(quick_violations)
            print(f"[步骤1] ✓ 快速检查发现 {len(quick_violations)} 个潜在问题")
        else:
            print("[步骤1] ✓ 快速检查未发现明显问题")
    else:
        # 加载代码规范配置
        rules = load_code_rules()
        
        if rules:
            # 精简规范描述
            rules_text = "\n".join([
                f"{i}. {rule.get('名称', '未命名')}：{rule.get('检查点', rule.get('描述', ''))}" 
                for i, rule in enumerate(rules, 1)
            ])
            
            system_prompt = f"""你是代码规范检查专家。检查以下代码变更是否违反规范：

                                {rules_text}

                                要求：
                                1. 仔细检查每一项规范
                                2. 发现违规直接输出："规范名称: 具体问题描述"
                                3. 无违规输出："通过"

                                只输出检查结果，不要解释。"""
            
            diff_sample = pr_diff
            truncate_note = ""
            
            prompts = [
                SystemMessage(content=system_prompt),
                HumanMessage(content=f"代码变更：\n```diff\n{diff_sample}\n```{truncate_note}")
            ]
            
            print("[步骤2] 🤖 调用大模型进行规范检查...")

            from src.utils.llm import llm, parser
            
            try:
                # 修改system_prompt，要求输出JSON格式
                system_prompt = f"""你是代码规范检查专家。检查以下代码变更是否违反规范：

{rules_text}

请以JSON格式输出检查结果，格式如下：
{{
    "passed": true/false,
    "violations": ["违规描述1", "违规描述2", ...]
}}

如果代码符合规范，violations为空数组。
如果发现违规，在violations中列出所有问题，格式："规范名称: 具体问题描述"
"""
                
                prompts[0] = SystemMessage(content=system_prompt)
                
                # 使用JSON格式的LLM（从配置读取重试和超时）
                expected_schema = {
                    "passed": bool,
                    "violations": list
                }
                
                llm_retry_config = CONFIG.get('llm', {}).get('retry', {})
                llm_timeout_config = CONFIG.get('llm', {}).get('timeout', {})
                max_retries = llm_retry_config.get('git_review', 2)
                timeout = llm_timeout_config.get('git_review', 300)
                
                print(f"[步骤2] 🤖 调用大模型进行规范检查（JSON格式，重试{max_retries}次，超时{timeout}秒）...")
                result = await parser.parse_json_with_retry(
                    conversation=prompts,
                    expected_schema=expected_schema,
                    max_retries=max_retries,
                    parser_name="git_review",
                    timeout=timeout
                )
                
                if result:
                    is_passed = result.get("passed", True)
                    result_violations = result.get("violations", [])
                    
                    if result_violations:
                        violations.extend(result_violations)
                        print(f"[步骤2] ✗ 检测到 {len(result_violations)} 个规范问题")
                    else:
                        print("[步骤2] ✓ 代码规范检查通过")
                    
                    writer({"rule_check_result": result})
                else:
                    # JSON解析失败，降级到快速检查
                    print(f"[步骤2] ⚠️ LLM返回格式错误，降级使用快速规则检查")
                    quick_violations = _quick_rule_check(pr_diff)
                    if quick_violations:
                        violations.extend(quick_violations)
                        print(f"[步骤2] ✓ 快速检查发现 {len(quick_violations)} 个潜在问题")
                    else:
                        print("[步骤2] ✓ 快速检查未发现明显问题")
                        
            except Exception as e:
                print(f"[步骤2] ⚠️ LLM调用失败: {str(e)[:100]}")
                print(f"[步骤2] 降级使用快速规则检查")
                quick_violations = _quick_rule_check(pr_diff)
                if quick_violations:
                    violations.extend(quick_violations)
                    print(f"[步骤2] ✓ 快速检查发现 {len(quick_violations)} 个潜在问题")
                else:
                    print("[步骤2] ✓ 快速检查未发现明显问题")
        else:
            print("[步骤2] ⚠️ 未配置代码规范，跳过检查")
    
    # 构建检查结果
    all_issues = []
    code_passed = True
    
    if violations:
        code_passed = False
        all_issues.append("❌ 代码规范检查未通过：")
        for violation in violations:
            all_issues.append(f"  • {violation}")
        all_issues.append("")
    
    # 提取修改的文件列表
    changed_files = []
    for file_info in pr_files:
        if isinstance(file_info, dict):
            file_path = file_info.get('path', file_info.get('filename', ''))
        else:
            file_path = str(file_info)
        if file_path:
            changed_files.append(file_path)
    
    print(f"\n[Git审查完成] 规范检查: {'✓ 通过' if code_passed else '✗ 未通过'}")
    print(f"[Git审查完成] 修改文件: {len(changed_files)} 个")
    print(f"[Git审查完成] 下一步: 代码分析智能体")
    print("="*60 + "\n")
    
    return {
        "code_check_passed": code_passed,
        "code_issues": all_issues,
        "changed_files": changed_files,
        "current_stage": "code_analysis"
    }

def _quick_rule_check(pr_diff: str) -> list:
    """快速规则检查 - 使用正则表达式，不依赖LLM"""
    violations = []
    
    # 常见问题模式
    quick_patterns = {
        'print语句': r'^\+.*\bprint\s*\(',
        'console.log': r'^\+.*\bconsole\.log\s*\(',
        'TODO标记': r'^\+.*\b(TODO|FIXME|XXX)\b',
        '硬编码密码': r'^\+.*(password|secret|key)\s*=\s*["\'][^"\']+["\']',
        '调试断点': r'^\+.*(debugger|breakpoint)',
    }
    
    for issue_name, pattern in quick_patterns.items():
        matches = re.findall(pattern, pr_diff, re.MULTILINE | re.IGNORECASE)
        if matches:
            violations.append(f"[低] 发现{issue_name}：{len(matches)}处")
    
    return violations
