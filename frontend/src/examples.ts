import type { IconName } from './Icon'
import type { Scenario } from './types'

// 2026-09-15：五个 SQL 样例已按精确条件检查数据库；法规问题已通过真实联调。
// 这里只预填问题，仍由 Agent 判断调用哪个工具，不在前端指定业务路由。
export const liveExamples: { id: string; icon: IconName; title: string; detail: string; question: string; scenario: Scenario }[] = [
  { id: 'knowledge', icon: 'book', title: '法规知识问答', detail: '了解采购规则，展开查看引用原文', question: '招标文件的提供期限最少是多少个工作日？', scenario: 'knowledge' },
  { id: 'registration', icon: 'company', title: '企业工商信息', detail: '查看企业登记信息与经营状态', question: '查询安徽海纳信息科技有限公司的工商信息', scenario: 'business' },
  { id: 'scope', icon: 'logo', title: '企业经营范围', detail: '按企业全称查看已收录的经营范围', question: '查询安徽海纳信息科技有限公司的经营范围', scenario: 'business' },
  { id: 'penalty', icon: 'shield', title: '企业处罚记录', detail: '核对已收录的处罚时间、事由和结果', question: '查询四川胤伟建筑工程有限公司的处罚记录', scenario: 'business' },
  { id: 'project', icon: 'chart', title: '项目中标详情', detail: '按项目编号查看中标供应商与金额', question: '查询项目编号 [350001]FJGGZY[GK]2024010 的中标详情', scenario: 'award' },
  { id: 'history', icon: 'clock', title: '企业中标历史', detail: '查看企业作为中标供应商的项目记录', question: '查询中国移动通信集团福建有限公司的中标历史', scenario: 'award' },
]
