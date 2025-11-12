from langgraph.config import get_stream_writer
from src.core.state import PRReviewState


def _add_change_analysis(report_lines: list, state: PRReviewState, analysis_conclusion: dict):
    """添加详细的变更分析到报告中"""
    # 获取diff和上下文信息
    pr_diff = state.get('pr_diff', '')
    all_collected_context = state.get('all_collected_context', {})
    impact_chain = state.get('impact_chain', [])
    
    # 分析diff中的变更类型
    lines = pr_diff.split('\n')
    deletions = [l for l in lines if l.startswith('-') and not l.startswith('---')]
    additions = [l for l in lines if l.startswith('+') and not l.startswith('+++')]
    
    # 识别删除的类/函数/变量
    deleted_items = []
    added_items = []
    
    import re
    
    for line in deletions:
        line_stripped = line.lstrip('-').strip()
        
        # C++类定义: class ClassName
        if re.match(r'\s*class\s+\w+', line_stripped):
            match = re.search(r'class\s+(\w+)', line_stripped)
            if match:
                deleted_items.append(('class', match.group(1)))
        # C++函数定义: ReturnType FunctionName(
        elif re.search(r'\b\w+\s+(\w+)\s*\([^)]*\)\s*(?:const)?\s*(?:override)?\s*{?', line_stripped):
            match = re.search(r'\b\w+\s+(\w+)\s*\(', line_stripped)
            if match and match.group(1) not in ['if', 'while', 'for', 'switch', 'catch']:
                deleted_items.append(('function/method', match.group(1)))
        # Python函数定义: def function_name(
        elif 'def ' in line_stripped:
            match = re.search(r'def\s+(\w+)\s*\(', line_stripped)
            if match:
                deleted_items.append(('function', match.group(1)))
    
    for line in additions:
        line_stripped = line.lstrip('+').strip()
        
        # 跳过非代码行
        if not line_stripped or line_stripped.startswith('//') or line_stripped.startswith('#'):
            continue
        
        # C++类定义
        if re.match(r'\s*class\s+\w+', line_stripped):
            match = re.search(r'class\s+(\w+)', line_stripped)
            if match:
                added_items.append(('class', match.group(1)))
        # Python函数定义: def function_name(
        elif 'def ' in line_stripped:
            match = re.search(r'def\s+(\w+)\s*\(', line_stripped)
            if match:
                added_items.append(('function', match.group(1)))
        # C++变量/对象声明（构造函数调用）: Type VarName(args);
        # 关键特征：括号后面是分号，不是函数体
        elif re.search(r'\b(\w+(?:\s*<[^>]+>)?)\s+(\w+)\s*\([^)]*\)\s*;', line_stripped):
            match = re.search(r'(\w+(?:\s*<[^>]+>)?)\s+(\w+)\s*\(', line_stripped)
            if match:
                var_type = match.group(1)
                var_name = match.group(2)
                # 排除控制流语句
                if var_type not in ['if', 'while', 'for', 'switch', 'catch', 'return']:
                    added_items.append(('variable/object', f"{var_name} ({var_type})"))
        # C++函数定义: ReturnType FunctionName(...) { 或带 const/override
        # 关键特征：括号后面是 {, const, override，或换行后是函数体
        elif re.search(r'\b\w+\s+(\w+)\s*\([^)]*\)\s*(?:const|override|\{)', line_stripped):
            match = re.search(r'\b\w+\s+(\w+)\s*\(', line_stripped)
            if match and match.group(1) not in ['if', 'while', 'for', 'switch', 'catch']:
                added_items.append(('function/method', match.group(1)))
    
    # 变更总结
    report_lines.append("")
    report_lines.append("变更总结：")
    
    # 根据审查结果生成总结
    has_critical_issues = analysis_conclusion.get('has_critical_issues', False)
    summary = analysis_conclusion.get('summary', '')
    
    if has_critical_issues:
        # 有问题的情况
        report_lines.append("  ⚠️ 本次变更存在以下问题：")
        critical_issues = analysis_conclusion.get('critical_issues', [])
        for issue in critical_issues[:3]:
            if isinstance(issue, dict):
                desc = issue.get('description', str(issue))
            else:
                desc = str(issue)
            report_lines.append(f"  - {desc}")
    else:
        # 通过审查的情况
        if deleted_items and all_collected_context:
            # 有删除且收集了上下文
            unused_count = sum(1 for name in [item[1] for item in deleted_items] 
                             if name in all_collected_context and 
                             all_collected_context[name].get('usage_count', 0) == 0)
            
            if unused_count == len(deleted_items):
                report_lines.append(f"  ✓ 删除了{len(deleted_items)}个未使用的定义，不影响现有功能")
            elif unused_count > 0:
                report_lines.append(f"  ⚠️ 删除了{len(deleted_items)}个定义，其中{unused_count}个未使用")
                used_count = len(deleted_items) - unused_count
                if used_count > 0:
                    report_lines.append(f"  ⚠️ 但仍有{used_count}个定义在其他地方被使用，需人工确认")
            else:
                report_lines.append(f"  ⚠️ 删除了{len(deleted_items)}个定义，均有其他地方使用")
        elif deleted_items:
            # 有删除但未收集上下文
            report_lines.append(f"  ℹ️ 删除了{len(deleted_items)}个定义，未进行深度依赖分析")
        
        if additions and not deletions:
            report_lines.append(f"  ✓ 仅新增代码，不影响现有功能")
        
        if summary:
            report_lines.append(f"  ✓ {summary}")
        
        if not (deleted_items or additions):
            report_lines.append("  ℹ️ 主要为代码调整，未涉及功能变更")
    
    # 迭代分析信息
    if impact_chain:
        report_lines.append("")
        report_lines.append(f"深度分析：进行了{len(impact_chain)}轮迭代追踪")
        
        for i, chain_entry in enumerate(impact_chain, 1):
            analysis_note = chain_entry.get('analysis_note', '')
            search_items = chain_entry.get('search_items', [])
            
            if analysis_note:
                report_lines.append(f"  第{i}轮: {analysis_note}")
            
            # 显示本轮搜索的项目及发现
            if search_items:
                report_lines.append(f"    搜索项: {', '.join([item.get('name', '') for item in search_items[:]])}")
                
        # 显示最终分析结论
        if analysis_conclusion:
            iteration_info = analysis_conclusion.get('iteration_info', {})
            total_iterations = iteration_info.get('total_iterations', len(impact_chain))
            report_lines.append(f"  ")
            report_lines.append(f"  最终结论（经{total_iterations}轮分析）:")
            
            final_summary = analysis_conclusion.get('summary', '')
            if final_summary:
                report_lines.append(f"    {final_summary}")
            
            # 如果有影响链，显示完整路径
            impact_chains = analysis_conclusion.get('impact_chains', [])
            if impact_chains:
                report_lines.append(f"    影响路径: {impact_chains[0]}")


def _generate_submitter_feedback(state: PRReviewState, code_passed: bool, rule_passed: bool,
                                 code_issues: list, rule_violations: list,
                                 analysis_conclusion: dict) -> str:
    """生成提交者反馈 - 关注审核结果、问题和修改建议"""
    
    # 基本信息
    source_branch = state.get('source_branch', '未知')
    target_branch = state.get('target_branch', '未知')
    changed_definitions = state.get('changed_definitions', [])
    
    # 审查状态
    all_passed = code_passed and rule_passed
    status_icon = "✅" if all_passed else "❌"
    status_text = "通过" if all_passed else "未通过"
    
    # 构建反馈
    feedback_lines = [
        f"{status_icon} 分支合并审查{status_text}",
        f"分支：{source_branch} → {target_branch}",
        ""
    ]
    
    if all_passed:
        # 通过的情况
        feedback_lines.extend([
            "🎉 恭喜！代码已通过自动审查",
            "✓ 代码规范检查：通过",
            "✓ 深度代码审查：通过",
            "",
            "您的合并请求将由管理员进行最终审核。"
        ])
    else:
        # 未通过的情况
        feedback_lines.append("❌ 审查未通过，请修改后重新提交")
        feedback_lines.append("")
        
        # 代码规范问题 - 更详细
        if not code_passed and code_issues:
            feedback_lines.append("【代码规范问题】")
            for i, issue in enumerate(code_issues, 1):
                feedback_lines.append(f"{i}. {issue}")
            feedback_lines.append("")
        
        # 深度审查问题 - 明确区分确定性问题和潜在风险
        if not rule_passed:
            # 获取问题信息
            critical_issues = analysis_conclusion.get('critical_issues', [])
            potential_risks = analysis_conclusion.get('potential_risks', [])
            impact_chains = analysis_conclusion.get('impact_chains', [])
            affected_features = analysis_conclusion.get('affected_features', [])
            
            # 1. 确定性问题（必须修复）
            if critical_issues:
                feedback_lines.append("【⚠️ 确定性问题】")
                for i, issue in enumerate(critical_issues, 1):
                    if isinstance(issue, dict):
                        desc = issue.get('description', str(issue))
                        severity = issue.get('severity', 'high')
                        file_ref = issue.get('file', '')
                        suggestion = issue.get('suggestion', '')
                        
                        feedback_lines.append(f"\n问题 {i}: {desc}")
                        feedback_lines.append(f"  严重度: {severity.upper()}")
                        if file_ref:
                            feedback_lines.append(f"  相关文件: {file_ref}")
                        if suggestion:
                            feedback_lines.append(f"  🔧 修复建议: {suggestion}")
                    else:
                        feedback_lines.append(f"\n问题 {i}: {issue}")
                feedback_lines.append("")
            
            # 2. 潜在风险（建议关注）
            if potential_risks:
                feedback_lines.append("【💡 潜在风险】")
                for i, risk in enumerate(potential_risks[:], 1):
                    if isinstance(risk, dict):
                        desc = risk.get('description', str(risk))
                        level = risk.get('level', 'medium')
                        suggestion = risk.get('suggestion', '')
                        
                        feedback_lines.append(f"{i}. [{level.upper()}] {desc}")
                        if suggestion:
                            feedback_lines.append(f"   建议: {suggestion}")
                    else:
                        feedback_lines.append(f"{i}. {risk}")
                feedback_lines.append("")
            
            # 3. 影响链分析
            if impact_chains:
                feedback_lines.append("【影响链分析】")
                for i, chain in enumerate(impact_chains[:], 1):
                    feedback_lines.append(f"  {i}) {chain}")
                feedback_lines.append("")
            
            # 4. 受影响功能
            if affected_features:
                feedback_lines.append(f"【受影响功能】{', '.join(affected_features[:])}")
                feedback_lines.append("")
        
        # 详细修改建议
        feedback_lines.append("【修改建议】")
        suggestions = []
        
        # 根据代码规范问题给出建议
        if not code_passed and code_issues:
            # 分析问题类型并给出针对性建议
            has_naming = any('命名' in str(issue) or 'naming' in str(issue).lower() for issue in code_issues)
            has_format = any('格式' in str(issue) or 'format' in str(issue).lower() for issue in code_issues)
            has_comment = any('注释' in str(issue) or 'comment' in str(issue).lower() for issue in code_issues)
            
            if has_naming:
                suggestions.append("修正不符合规范的命名（建议参考项目命名规范文档）")
            if has_format:
                suggestions.append("调整代码格式（建议使用IDE自动格式化工具）")
            if has_comment:
                suggestions.append("补充必要的代码注释，解释复杂逻辑")
            if not (has_naming or has_format or has_comment):
                suggestions.append("修复上述代码规范问题")
        
        # 根据深度审查问题给出建议
        if not rule_passed:
            critical_issues = analysis_conclusion.get('critical_issues', [])
            deleted_defs = [d for d in changed_definitions if d.get('change_type') == 'deleted']
            modified_defs = [d for d in changed_definitions if d.get('change_type') == 'modified']
            
            if deleted_defs:
                suggestions.append(f"检查删除的{len(deleted_defs)}个定义是否仍被其他代码使用")
                # 列出被删除的关键定义
                key_deletions = [d['name'] for d in deleted_defs[:3]]
                if key_deletions:
                    suggestions.append(f"  特别关注：{', '.join(key_deletions)}")
            
            if modified_defs:
                suggestions.append(f"验证修改的{len(modified_defs)}个定义不会破坏现有功能")
            
            if critical_issues:
                suggestions.append("根据上述影响链分析，逐一修复确定性问题")
                suggestions.append("确保所有依赖该代码的模块都已相应更新")
        
        for i, suggestion in enumerate(suggestions, 1):
            feedback_lines.append(f"{i}. {suggestion}")
        
        feedback_lines.append("")
        feedback_lines.append("💡 提示：如对审查结果有疑问或需要帮助，请联系管理员。")
    
    return "\n".join(feedback_lines)


def _generate_admin_feedback(state: PRReviewState, code_passed: bool, rule_passed: bool, 
                             code_issues: list, rule_violations: list,
                             analysis_conclusion: dict) -> str:
    """生成管理员反馈 - 关注变更总结、潜在问题和审核建议"""
    
    # 基本信息
    repo_name = state.get('repo_name', '未知')
    source_branch = state.get('source_branch', '未知')
    target_branch = state.get('target_branch', '未知')
    feishu_user_name = state.get('feishu_user_name', '未知')
    
    # 变更统计
    pr_size = state.get('pr_size', 'unknown')
    pr_stats = state.get('pr_stats', {})
    changed_files = state.get('changed_files', [])
    changed_definitions = state.get('changed_definitions', [])
    
    files_count = pr_stats.get('files_count', len(changed_files))
    additions = pr_stats.get('additions', 0)
    deletions = pr_stats.get('deletions', 0)
    
    # 分类定义变更
    added_defs = [d for d in changed_definitions if d.get('change_type') == 'added/modified']
    deleted_defs = [d for d in changed_definitions if d.get('change_type') == 'deleted']
    
    # 审查状态
    all_passed = code_passed and rule_passed
    status_icon = "✅" if all_passed else "❌"
    
    # 构建报告
    report_lines = [
        f"{status_icon} 分支合并审查报告",
        f"仓库：{repo_name} | 分支：{source_branch} → {target_branch}",
        f"提交者：{feishu_user_name}",
        ""
    ]
    
    # 详细变更摘要
    report_lines.append("【变更摘要】")
    report_lines.append(f"规模：{pr_size.upper()} | 文件：{files_count}个 | 代码行：+{additions}/-{deletions}")
    report_lines.append("")
    
    # 文件变更详情
    if changed_files:
        report_lines.append("修改的文件：")
        # 按文件类型分组
        file_types = {}
        for f in changed_files[:]:
            ext = f.split('.')[-1] if '.' in f else 'other'
            if ext not in file_types:
                file_types[ext] = []
            file_types[ext].append(f)
        
        for ext, files in sorted(file_types.items()):
            report_lines.append(f"  [{ext}] {', '.join(files[:])}")
    
    # 定义变更详情
    if added_defs or deleted_defs:
        report_lines.append("定义变更：")
        
        if deleted_defs:
            report_lines.append(f"  删除 ({len(deleted_defs)}个):")
            for d in deleted_defs[:]: 
                name = d.get('name', '未知')
                def_type = d.get('type', '定义')
                file_path = d.get('file', '未知文件')
                report_lines.append(f"    - {def_type}: {name}")
                report_lines.append(f"      位置: {file_path}")
            report_lines.append("")
        
        if added_defs:
            truly_added = [d for d in added_defs if not d.get('is_modification', False)]
            modified = [d for d in added_defs if d.get('is_modification', False)]
            
            if truly_added:
                report_lines.append(f"  新增 ({len(truly_added)}个):")
                for d in truly_added[:]:
                    name = d.get('name', '未知')
                    def_type = d.get('type', '定义')
                    file_path = d.get('file', '未知文件')
                    report_lines.append(f"    - {def_type}: {name} (在 {file_path})")
            
            if modified:
                report_lines.append(f"  修改 ({len(modified)}个):")
                for d in modified[:]:
                    name = d.get('name', '未知')
                    def_type = d.get('type', '定义')
                    change_desc = d.get('change_description', '详情未知')
                    report_lines.append(f"    - {def_type}: {name}")
                    report_lines.append(f"      变更: {change_desc}")
        
        report_lines.append("")
    
    # 审查结果
    report_lines.append("【审查结果】")
    report_lines.append(f"{'✓' if code_passed else '✗'} 代码规范：{'通过' if code_passed else '未通过'}")
    report_lines.append(f"{'✓' if rule_passed else '✗'} 深度审查：{'通过' if rule_passed else '未通过'}")
    
    # 未通过原因
    if not all_passed:
        report_lines.append("")
        if not code_passed and code_issues:
            report_lines.append("代码规范问题：")
            for issue in code_issues[:]:  
                report_lines.append(f"  • {issue}")
        
        if not rule_passed and rule_violations:
            report_lines.append("深度审查问题：")
            for violation in rule_violations[:]:  
                report_lines.append(f"  • {violation}")
    
    report_lines.append("")
    
    report_lines.append("【变更分析】")
    _add_change_analysis(report_lines, state, analysis_conclusion)
    report_lines.append("")
    
    # 详细潜在风险分析
    potential_risks = analysis_conclusion.get('potential_risks', [])
    critical_issues = analysis_conclusion.get('critical_issues', [])
    impact_chains = analysis_conclusion.get('impact_chains', [])
    affected_features = analysis_conclusion.get('affected_features', [])
    
    report_lines.append("【潜在风险】")
    
    if critical_issues:
        report_lines.append("确定性问题：")
        for i, issue in enumerate(critical_issues[:], 1):
            if isinstance(issue, dict):
                desc = issue.get('description', str(issue))
                severity = issue.get('severity', 'medium')
                file_ref = issue.get('file', '')
                report_lines.append(f"  {i}. [严重度:{severity.upper()}] {desc}")
                if file_ref:
                    report_lines.append(f"     相关文件: {file_ref}")
            else:
                report_lines.append(f"  {i}. {issue}")
        report_lines.append("")
    
    if impact_chains:
        report_lines.append("影响链分析：")
        for i, chain in enumerate(impact_chains[:], 1):
            report_lines.append(f"  {i}) {chain}")
        report_lines.append("")
    
    if affected_features:
        report_lines.append(f"受影响功能：{', '.join(affected_features[:])}")
        report_lines.append("")
    
    # 其他风险
    if potential_risks:
        report_lines.append("其他需要关注的风险：")
        for i, risk in enumerate(potential_risks[:], 1):
            desc = risk.get('description', str(risk)) if isinstance(risk, dict) else str(risk)
            level = risk.get('level', 'medium') if isinstance(risk, dict) else 'medium'
            report_lines.append(f"  {i}. [{level}] {desc}")
        report_lines.append("")
    
    # 代码变更风险评估
    risk_indicators = []
    if deleted_defs:
        risk_indicators.append(f"删除定义风险：删除了{len(deleted_defs)}个定义，需验证无遗留调用")
    if pr_size in ['large', 'xlarge']:
        risk_indicators.append(f"规模风险：{pr_size.upper()}级别变更，可能影响多个模块")
    
    modified_defs = [d for d in changed_definitions if d.get('change_type') == 'modified']
    if modified_defs:
        risk_indicators.append(f"接口变更风险：修改了{len(modified_defs)}个定义，需检查调用方兼容性")
    
    if risk_indicators:
        report_lines.append("代码变更风险评估：")
        for indicator in risk_indicators:
            report_lines.append(f"  • {indicator}")
        report_lines.append("")
    
    if not (critical_issues or impact_chains or affected_features or potential_risks or risk_indicators):
        report_lines.append("未发现明显风险")
        report_lines.append("")
    
    # 人工审核建议
    report_lines.append("【审核建议】")
    suggestions = []
    
    # 1. 代码变更审查
    if pr_size in ['large', 'xlarge']:
        suggestions.append(f"PR规模为{pr_size.upper()}级别，建议重点审查架构变更")
    
    if deleted_defs:
        suggestions.append(f"包含{len(deleted_defs)}个删除操作，需确认无遗留调用")
        # 列出关键删除项
        key_deletions = [d.get('name', '未知') for d in deleted_defs[:3]]
        if key_deletions:
            suggestions.append(f"  重点关注删除的定义：{', '.join(key_deletions)}")
    
    modified_defs = [d for d in changed_definitions if d.get('change_type') == 'modified']
    if modified_defs:
        suggestions.append(f"包含{len(modified_defs)}个定义修改，需验证调用方兼容性")
    
    # 2. 影响范围检查
    if affected_features:
        suggestions.append(f"受影响功能模块：{', '.join(affected_features[:3])}")
    
    if impact_chains:
        suggestions.append(f"存在{len(impact_chains)}条影响链，需验证影响范围")
    
    # 3. 特殊文件检查
    config_files = [f for f in changed_files if any(ext in f.lower() for ext in ['.yaml', '.yml', '.json', '.env', '.config'])]
    if config_files:
        suggestions.append(f"修改了{len(config_files)}个配置文件，需验证配置正确性")
    
    test_files = [f for f in changed_files if 'test' in f.lower()]
    if test_files:
        suggestions.append(f"修改了{len(test_files)}个测试文件，建议运行完整测试")
    elif deleted_defs or modified_defs:
        suggestions.append("建议补充或更新相关测试用例")

    # 4. 默认建议
    if not suggestions:
        suggestions.append("代码变更规模适中，进行常规审查即可")
    
    for i, suggestion in enumerate(suggestions, 1):
        report_lines.append(f"  {i}. {suggestion}")
    
    report_lines.append("")
    
    # 最终建议
    if all_passed:
        report_lines.append("✅ 建议：可以合并（需人工最终确认）")
    else:
        report_lines.append("❌ 建议：需修复问题后重新提交")
    
    return "\n".join(report_lines)


def decision_node(state: PRReviewState) -> PRReviewState:
    """决策节点"""
    print("=== 决策节点 ===")
    writer = get_stream_writer()
    writer({"stage": "decision", "status": "started"})
    
    code_passed = state.get("code_check_passed", False)
    code_issues = state.get("code_issues", [])
    
    # 获取双智能体分析结论
    analysis_conclusion = state.get("analysis_conclusion", {})
    has_critical_issues = analysis_conclusion.get('has_critical_issues', False)
    critical_issues = analysis_conclusion.get('critical_issues', [])
    summary = analysis_conclusion.get('summary', '')
    confidence = analysis_conclusion.get('confidence', 0)
    
    # 过滤空问题
    valid_critical_issues = [
        issue for issue in critical_issues 
        if issue and str(issue).strip()
    ]
    
    # 深度分析通过条件：双重验证，确保逻辑一致性
    # 1. has_critical_issues 标志必须为 False
    # 2. critical_issues 列表必须为空（过滤空值后）
    # 这样即使LLM误判布尔标志，只要列表有内容仍会正确判定为未通过
    rule_passed = not has_critical_issues and len(valid_critical_issues) == 0
    
    # 构建深度分析结果
    rule_violations = []
    if has_critical_issues:
        rule_violations.append("❌ 深度分析发现确定性问题：")
        for idx, issue in enumerate(critical_issues, 1):
            if isinstance(issue, dict):
                desc = issue.get('description', str(issue))
            else:
                desc = str(issue)
            rule_violations.append(f"  {idx}. {desc}")
        if summary:
            rule_violations.append(f"\n总结：{summary}")
        if confidence:
            rule_violations.append(f"置信度：{confidence}%")
    elif analysis_conclusion:
        # 有分析但无问题
        rule_violations.append("✅ 深度分析通过")
        if summary:
            rule_violations.append(f"  {summary}")
    
    # 生成双重反馈
    submitter_feedback = _generate_submitter_feedback(
        state, code_passed, rule_passed, 
        code_issues, rule_violations, analysis_conclusion
    )
    
    admin_feedback = _generate_admin_feedback(
        state, code_passed, rule_passed, 
        code_issues, rule_violations, analysis_conclusion
    )
    
    decision = "approve" if (code_passed and rule_passed) else "reject"
    
    writer({"decision": decision, "feedback_generated": True})
    
    return {
        "final_decision": decision,
        "feedback_message": submitter_feedback,
        "submitter_feedback": submitter_feedback,
        "admin_feedback": admin_feedback,
        "current_stage": "feishu_feedback"
    }
