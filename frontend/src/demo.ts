import type { Result, Scenario } from './types'
import { adaptFinal } from './api/results'

export const scenarios: { value: Scenario; label: string }[] = [
  { value: 'knowledge', label: '法规问答' }, { value: 'award', label: '中标查询' },
  { value: 'business', label: '企业资料与处罚' },
  { value: 'general', label: '能力介绍' }, { value: 'clarify', label: '需要补充条件' },
  { value: 'partial', label: '部分完成' }, { value: 'error', label: '服务错误' },
  { value: 'timeout', label: '请求超时' }, { value: 'disconnect', label: '正文后连接中断' },
]
export const examples = [
  { scenario: 'knowledge' as const, icon: 'book' as const, title: '法规有出处', detail: '了解采购规则，展开查看引用原文', question: '招标文件中需要关注哪些内容？' },
  { scenario: 'award' as const, icon: 'chart' as const, title: '记录可核验', detail: '按项目编号查看中标供应商与金额', question: '查询项目 DEMO2026001 的中标情况' },
  { scenario: 'general' as const, icon: 'spark' as const, title: '从这里开始', detail: '了解助手的能力与使用边界', question: '你可以帮我做什么？' },
]
const citations = [
  { index: 1, title: '采购文件阅读指引（虚构示例）', chapter: '第一节 · 文件组成', text: '本段仅用于演示引用卡片：阅读采购文件时，可以分别整理项目需求、参与条件和响应材料。此内容为虚构的界面样例，不是法律法规原文，不作为业务依据。' },
  { index: 2, title: '采购流程说明（虚构示例）', chapter: '第二节 · 时间安排', text: '本段仅用于演示展开后的完整引用：将公告、文件获取、提交截止等时间信息分别记录。此内容为虚构的界面样例，不对应真实法规或项目。' },
]
export function resultFor(scenario: Scenario): Result {
  if (scenario === 'business') {
    const answer = '以下为虚构的企业资料、经营范围和处罚记录，用于检查分类展示。\n\n同一轮回答可包含多个业务分组；企业中标历史的空查询和项目查询失败分别展示。所有名称及记录均为界面样例，不对应真实企业。'
    return adaptFinal({ answer, business_result: { answer, execution_status: 'partial', data: {
      actions: [
        { step_id: 's1', tool: 'query_company_registration', ok: true },
        { step_id: 's2', tool: 'query_company_business_scope', ok: true },
        { step_id: 's3', tool: 'query_company_penalty', ok: true },
        { step_id: 's4', tool: 'query_company_award_history', ok: true, empty: true, exact_scope: true },
        { step_id: 's5', tool: 'query_project_award', ok: false },
      ],
      records: [
        { step_id: 's1', record_ref: 'demo-company', company_name: '虚构样例企业（仅供界面演示）', credit_code: null, legal_person: '示例负责人', registered_capital: null, establish_date: '2020-01-01', business_status: '示例状态', industry: '示例行业' },
        { step_id: 's2', record_ref: 'demo-scope', company_name: '虚构样例企业（仅供界面演示）', business_scope: '本段为经营范围长文本的虚构占位。用于观察多行内容的换行、卡片高度与移动端排版。\n示例业务：办公设备服务、信息咨询、项目资料整理。实际经营范围应以查询返回的原始记录为准。' },
        { step_id: 's3', record_ref: 'demo-penalty', company_name: '虚构样例企业（仅供界面演示）', penalty_date: '2026-01-01', illegal_behavior: '仅供布局检查的虚构行为描述，不是对任何企业的事实认定。长段落在表格单元格内换行。', penalty_result: '虚构处理结果，不对应真实处罚', law_enforcement_unit: null },
      ],
    } } })
  }
  if (scenario === 'award') return {
    status: 'complete', citations: [],
    answer: '已找到项目 DEMO2026001 的示例中标记录。\n\n项目为“城市公共服务中心办公设备采购”，中标供应商为“示例办公设备有限公司”。具体信息见下表。\n\n这些记录均为虚构数据，仅用于查看页面布局。金额保留示例原始值，单位未核定。',
    records: [{ id: 'DEMO2026001', name: '城市公共服务中心办公设备采购', supplier: '示例办公设备有限公司', amount: '286,000.00', date: '2026-09-01' }],
  }
  if (scenario === 'general') return {
    status: 'complete', citations: [], records: [],
    answer: '你好，我是招投标智能助手。\n\n你可以从法规问答、企业信息核验和中标记录查询开始。法规回答配合来源阅读，结构化查询通过表格呈现。\n\n当前页面为静态演示，输入不会发送给 Agent。你可以切换上方的演示场景，体验回答、引用、停止生成和错误恢复。',
  }
  if (scenario === 'clarify') return {
    status: 'clarify', citations: [], records: [],
    answer: '还需要一个明确的项目编号。\n\n请补充你想查询的项目编号，例如 DEMO2026001。当前项目中标查询需要按编号检索，仅提供项目名称还不足以执行。\n\n这是“请求正常结束，但需要补充条件”的界面示例。',
  }
  if (scenario === 'partial') return {
    status: 'partial', citations: citations.slice(0, 1), records: [],
    answer: '本次示例已整理采购文件的阅读要点。【来源1】\n\n关联项目的中标数据暂未返回，因此这部分查询尚未完成。已返回的内容保留在此处，可以稍后重试。\n\n本段为虚构的部分完成示例，不代表真实服务状态。',
  }
  return {
    status: 'complete', citations, records: [],
    answer: '可以先从三个方面梳理招标文件：\n\n一、项目需求与交付范围\n明确采购内容、技术要求及交付条件，将需要核对的条目整理成清单。【来源1】\n\n二、参与条件与响应材料\n逐项记录要求提交的证明材料，区分资格条件与评审内容。【来源1】\n\n三、关键时间与提交方式\n关注文件获取、提交截止等时间节点，并核对提交方式。【来源2】\n\n以上为界面演示内容，不是法规解答。请展开下方引用，查看虚构的来源样例。',
  }
}
export const stages = ['正在处理问题', '正在调用查询工具', '查询结果已返回', '正在整理结果', '正在校验结果', '校验结束，正在发送正文']
