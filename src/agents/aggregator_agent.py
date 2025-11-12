"""
PR汇总智能体 - 汇总所有子PR的审查结果
"""

from typing import List, Dict
from langchain_core.messages import SystemMessage, HumanMessage
from langgraph.config import get_stream_writer
from src.core.state import PRReviewState
# 导入完整的变更分析函数
from src.agents.decision_agent import _add_change_analysis


def _add_sub_pr_change_analysis(report_lines: list, result: dict):
    """为子PR添加完整的变更分析"""
    # 创建临时state用于调用_add_change_analysis
    temp_state = {
        'pr_diff': result.get('pr_diff', ''),
        'all_collected_context': result.get('all_collected_context', {}),
        'impact_chain': result.get('impact_chain', [])
    }
    
    analysis_conclusion = result.get('analysis_conclusion', {})
    
    # 创建临时列表收集变更分析
    temp_lines = []
    _add_change_analysis(temp_lines, temp_state, analysis_conclusion)
    
    # 将分析结果添加到报告中，每行前面加两个空格缩进
    for line in temp_lines:
        report_lines.append(f"  {line}")


async def pr_aggregator_node(state: PRReviewState) -> PRReviewState:
    """PR汇总智能体节点
    
    职责：
    1. 收集所有子PR的审查结果
    2. 使用LLM进行整合分析
    3. 生成最终的综合审查报告
    """
    print("\n" + "="*60)
    print("=== PR汇总智能体 ===")
    print("="*60)
    writer = get_stream_writer()
    writer({"stage": "pr_aggregator", "status": "started"})
    
    sub_pr_results = state.get("sub_pr_results", [])
    
    if not sub_pr_results:
        print("[错误] 没有子PR审查结果可供汇总")
        return {
            "current_stage": "aggregator_failed",
            "feedback_message": "没有子PR审查结果"
        }
    
    print(f"[步骤1] 收集到 {len(sub_pr_results)} 个子PR的审查结果")
    
    # 1. 统计各子PR的审查状态
    print("\n[步骤2] 统计子PR审查状态...")
    total_count = len(sub_pr_results)
    approved_count = 0
    rejected_count = 0
    issues_summary = []
    
    for i, result in enumerate(sub_pr_results, 1):
        sub_pr_title = result.get('title', f'子PR-{i}')
        decision = result.get('final_decision', 'unknown')
        issues = result.get('issues', [])
        
        print(f"  子PR {i}: {sub_pr_title}")
        print(f"    决策: {decision}")
        
        if decision in ['approve', 'approved']:  # 兼容两种写法
            approved_count += 1
            print(f"    状态: ✓ 通过")
        else:
            rejected_count += 1
            print(f"    状态: ✗ 未通过")
            if issues:
                print(f"    问题数: {len(issues)}")
                issues_summary.append({
                    'sub_pr': sub_pr_title,
                    'issues': issues
                })
    
    print(f"\n[统计] 总计: {total_count} 个子PR")
    print(f"[统计]   通过: {approved_count} 个")
    print(f"[统计]   未通过: {rejected_count} 个")
    
    # 2. 生成双重反馈报告
    print("\n[步骤3] 生成综合审查报告...")
    
    # 给提交者的报告（关注问题和修改建议）
    submitter_report = await _generate_submitter_report(
        sub_pr_results, 
        approved_count, 
        rejected_count,
        issues_summary
    )
    
    # 给管理员的报告（详细技术分析）
    admin_report = await _generate_admin_report(
        sub_pr_results, 
        approved_count, 
        rejected_count,
        issues_summary
    )
    
    # 3. 确定最终决策
    # 决策逻辑：如果所有子PR都通过，则整体通过；否则需要修改
    final_decision = "approved" if rejected_count == 0 else "needs_changes"
    
    print(f"\n[最终决策] {final_decision.upper()}")
    print("="*60 + "\n")
    
    writer({"aggregation_result": {
        "total_sub_prs": total_count,
        "approved": approved_count,
        "rejected": rejected_count,
        "final_decision": final_decision
    }})
    
    return {
        "final_decision": final_decision,
        "submitter_feedback": submitter_report,
        "admin_feedback": admin_report,
        "current_stage": "feishu_feedback"
    }


async def _generate_submitter_report(
    sub_pr_results: List[Dict], 
    approved_count: int, 
    rejected_count: int,
    issues_summary: List[Dict]
) -> str:
    """生成提交者报告 - 关注审核结果、问题和具体修复方法"""
    
    report_lines = []
    all_passed = (rejected_count == 0)
    
    # 标题
    status_icon = "✅" if all_passed else "❌"
    status_text = "通过" if all_passed else "未通过"
    report_lines.append(f"{status_icon} PR审查{status_text}")
    report_lines.append(f"")
    report_lines.append(f"共拆分为 {len(sub_pr_results)} 个子PR进行深度分析")
    report_lines.append(f"")
    
    if all_passed:
        # 所有子PR都通过
        report_lines.append("🎉 无确定性问题，所有子PR均已通过自动审查")
        report_lines.append(f"✓ {approved_count} 个子PR全部通过")
        report_lines.append("")
        report_lines.append("您的合并请求将由管理员进行最终审核。")
    else:
        # 有未通过的子PR
        report_lines.append("❌ 审查未通过，请修改后重新提交")
        report_lines.append(f"✓ 通过: {approved_count} 个子PR")
        report_lines.append(f"✗ 未通过: {rejected_count} 个子PR")
        report_lines.append("")
        
        # 详细列出每个未通过子PR的问题和修复建议
        report_lines.append("【问题详情与修复建议】")
        report_lines.append("")
        
        problem_index = 1
        for i, result in enumerate(sub_pr_results, 1):
            decision = result.get('final_decision', 'unknown')
            if decision not in ['approve', 'approved']:
                analysis_conclusion = result.get('analysis_conclusion', {})
                
                # 获取确定性问题和潜在风险
                critical_issues = analysis_conclusion.get('critical_issues', [])
                potential_risks = analysis_conclusion.get('potential_risks', [])
                impact_chains = analysis_conclusion.get('impact_chains', [])
                
                # 确定性问题
                if critical_issues:
                    report_lines.append(" ⚠️ 确定性问题（必须修复）")
                    for issue in critical_issues:
                        if isinstance(issue, dict):
                            desc = issue.get('description', str(issue))
                            severity = issue.get('severity', 'high')
                            file_ref = issue.get('file', '')
                            suggestion = issue.get('suggestion', '')
                            
                            report_lines.append(f"\n问题 {problem_index}: {desc}")
                            report_lines.append(f"- 严重度: {severity.upper()}")
                            if file_ref:
                                report_lines.append(f"- 相关文件: {file_ref}")
                            
                            # 修复建议
                            if suggestion:
                                report_lines.append(f"- 🔧 修复方法: {suggestion}")
                            else:
                                # 根据问题描述推断修复建议
                                if '删除' in desc and '使用' in desc:
                                    report_lines.append(f"- 🔧 修复方法: 恢复被删除的定义，或更新所有使用该定义的代码")
                                elif '修改' in desc and '接口' in desc:
                                    report_lines.append(f"- 🔧 修复方法: 检查所有调用方，确保参数和返回值兼容")
                                else:
                                    report_lines.append(f"- 🔧 修复方法: 根据上述问题描述修正代码逻辑")
                            
                            problem_index += 1
                        else:
                            report_lines.append(f"\n问题 {problem_index}: {issue}")
                            report_lines.append(f"- 🔧 修复方法: 请仔细检查相关代码并修复")
                            problem_index += 1
                    report_lines.append("")
                
                # 潜在风险
                if potential_risks:
                    report_lines.append(" 💡 潜在风险（建议关注）")
                    for risk in potential_risks[:]:
                        if isinstance(risk, dict):
                            desc = risk.get('description', str(risk))
                            level = risk.get('level', 'medium')
                            suggestion = risk.get('suggestion', '')
                            
                            report_lines.append(f"- [{level.upper()}] {desc}")
                            if suggestion:
                                report_lines.append(f"  建议: {suggestion}")
                        else:
                            report_lines.append(f"- {risk}")
                    report_lines.append("")
                
                # 影响链
                if impact_chains:
                    report_lines.append(" 影响链")
                    for chain in impact_chains[:3]:
                        report_lines.append(f"- {chain}")
                    report_lines.append("")
                
                report_lines.append("---")
                report_lines.append("")
        
        # 总体修改建议
        report_lines.append("")
        report_lines.append("【总体修改建议】")
        report_lines.append("1. 按照上述每个问题的具体修复方法逐一处理")
        report_lines.append("2. 修复确定性问题后，关注潜在风险并进行验证")
        report_lines.append("3. 检查影响链中提到的所有相关文件")
        report_lines.append("4. 完成修改后重新提交，系统将再次进行审查")
        report_lines.append("")
    
    return "\n".join(report_lines)


async def _generate_admin_report(
    sub_pr_results: List[Dict], 
    approved_count: int, 
    rejected_count: int,
    issues_summary: List[Dict]
) -> str:
    """生成管理员报告 - 详细技术分析"""
    
    report_lines = []
    
    # ===== 1. 整体审查结果 =====
    report_lines.append(" 📊 整体审查结果")
    report_lines.append(f"")
    report_lines.append(f"共拆分为 {len(sub_pr_results)} 个子PR进行深度分析")
    report_lines.append(f"- ✓ 通过: {approved_count} 个")
    report_lines.append(f"- ✗ 未通过: {rejected_count} 个")
    report_lines.append(f"")
    
    # ===== 2. 各子PR审查详情 =====
    report_lines.append(" 🔍 各子PR审查详情")
    report_lines.append("")
    
    for i, result in enumerate(sub_pr_results, 1):
        title = result.get('title', f'子PR-{i}')
        decision = result.get('final_decision', 'unknown')
        
        # 获取详细分析信息
        pr_stats = result.get('pr_stats', {})
        analysis_conclusion = result.get('analysis_conclusion', {})
        
        status_icon = "✓" if decision in ['approve', 'approved'] else "✗"
        status_text = "通过" if decision in ['approve', 'approved'] else "未通过"
        
        report_lines.append(f" {i}. {title}")
        report_lines.append(f"- 审查状态: {status_icon} {status_text}")
        
        # 代码统计
        additions = pr_stats.get('additions', 0)
        deletions = pr_stats.get('deletions', 0)
        files_count = pr_stats.get('files_count', 0)
        changed_files = result.get('changed_files', [])
        
        if additions > 0 or deletions > 0:
            report_lines.append(f"- 代码变更: {files_count}个文件, +{additions}/-{deletions}行")
        
        # 变更文件列表
        if changed_files:
            report_lines.append(f"- 变更文件:")
            for file_path in changed_files:
                report_lines.append(f"  • {file_path}")
        
        # 变更分析（类似单PR报告）
        if analysis_conclusion:
            _add_sub_pr_change_analysis(report_lines, result)
        
        # 问题列表 - 明确区分确定性问题和潜在风险
        critical_issues = analysis_conclusion.get('critical_issues', [])
        potential_risks = analysis_conclusion.get('potential_risks', [])
        
        if critical_issues:
            report_lines.append(f"- ⚠️ 确定性问题 ({len(critical_issues)}个):")
            for issue in critical_issues[:]:
                if isinstance(issue, dict):
                    desc = issue.get('description', str(issue))
                    severity = issue.get('severity', 'medium')
                    file_ref = issue.get('file', '')
                    report_lines.append(f"  - [严重度:{severity.upper()}] {desc}")
                    if file_ref:
                        report_lines.append(f"    相关文件: {file_ref}")
                else:
                    report_lines.append(f"  - {issue}")
        
        if potential_risks:
            report_lines.append(f"- 💡 潜在风险 ({len(potential_risks)}个):")
            for risk in potential_risks[:]:
                if isinstance(risk, dict):
                    desc = risk.get('description', str(risk))
                    level = risk.get('level', 'medium')
                    report_lines.append(f"  - [{level.upper()}] {desc}")
                else:
                    report_lines.append(f"  - {risk}")
        
        if not critical_issues and not potential_risks and decision in ['approve', 'approved']:
            report_lines.append(f"- 审查结果: ✓ 无问题发现，代码质量良好")
        
        report_lines.append("")
    
    # ===== 3. 综合分析 =====
    report_lines.append(" 💡 综合分析与建议")
    report_lines.append("")
    
    if rejected_count == 0:
        # 所有子PR都通过
        report_lines.append(" ✅ 审查结论")
        report_lines.append(f"所有 {len(sub_pr_results)} 个子PR均无确定性问题，审查通过。")
        report_lines.append("")
        report_lines.append(" 建议")
        report_lines.append("- 代码可以合并")
        report_lines.append("- 建议在合并前进行最终人工复核")
        report_lines.append("- 确保所有单元测试通过")
    else:
        # 有未通过的子PR
        report_lines.append(" ⚠️ 审查结论")
        report_lines.append(f"共 {rejected_count} 个子PR未通过审查，需要修复问题后重新提交。")
        report_lines.append("")
    
    return "\n".join(report_lines)
