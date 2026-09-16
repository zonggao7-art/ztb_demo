import type { BusinessStatus, Citation, Result, ResultGroup } from '../types'
import { isObject, StreamProtocolError } from './sse'

type Spec = { kind: ResultGroup['kind']; title: string; fields: [string, string][] }
const awardFields: [string, string][] = [['project_name', '项目名称'], ['project_number', '项目编号'], ['purchaser', '采购人'], ['successful_bidder', '中标供应商'], ['winning_amount', '金额原始值'], ['winning_date', '中标日期']]
const specs: Record<string, Spec> = {
  query_company_registration: { kind: 'company', title: '企业工商信息', fields: [['company_name', '企业名称'], ['credit_code', '统一社会信用代码'], ['legal_person', '法定代表人'], ['registered_capital', '注册资本原始记录'], ['establish_date', '成立日期'], ['business_status', '经营状态'], ['industry', '行业'], ['province', '省份'], ['city', '城市']] },
  query_company_business_scope: { kind: 'scope', title: '经营范围', fields: [['company_name', '企业名称'], ['business_scope', '经营范围']] },
  query_company_penalty: { kind: 'penalty', title: '处罚记录', fields: [['company_name', '企业名称'], ['penalty_date', '处罚日期'], ['illegal_behavior', '违法行为记录'], ['penalty_result', '处罚结果'], ['law_enforcement_unit', '执法单位']] },
  query_project_award: { kind: 'award', title: '项目中标情况', fields: awardFields },
  query_company_award_history: { kind: 'award', title: '企业中标历史', fields: awardFields },
}
export const toolTitle = (name: unknown) => typeof name === 'string' && Object.hasOwn(specs, name) ? specs[name].title : name === 'knowledge_qa' ? '法规知识库' : '业务资料'
export const cellText = (value: unknown): string => value == null || (typeof value === 'string' && !value.trim()) ? '未收录'
  : typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean' ? String(value) : '未收录'
const arrayField = (obj: Record<string, unknown>, key: string): unknown[] => {
  if (obj[key] === undefined) return []
  if (!Array.isArray(obj[key])) throw new StreamProtocolError(`${key} 格式不匹配`)
  return obj[key] as unknown[]
}

export function adaptFinal(payload: Record<string, unknown>): Result {
  const business = payload.business_result
  if (!isObject(business) || typeof payload.answer !== 'string' || business.answer !== payload.answer
    || !['complete', 'clarify', 'partial', 'unsupported', 'insufficient_evidence'].includes(String(business.execution_status))) {
    throw new StreamProtocolError('最终结果不完整')
  }
  if (!isObject(business.data)) throw new StreamProtocolError('最终数据格式不匹配')
  const data = business.data
  const records = arrayField(data, 'records'), actions = arrayField(data, 'actions'), sources = arrayField(data, 'citations')
  const byStep = new Map<string, Record<string, unknown>[]>()
  let unclassified = false
  for (const action of actions) {
    if (!isObject(action) || typeof action.step_id !== 'string') { unclassified = true; continue }
    byStep.set(action.step_id, [...(byStep.get(action.step_id) ?? []), action])
  }
  const groups: ResultGroup[] = [], handled = new Set<unknown>()
  for (const [step, candidates] of byStep) {
    if (candidates.length !== 1) { unclassified = true; continue }
    const action = candidates[0], name = action.tool
    if (name === 'knowledge_qa') continue
    if (typeof name !== 'string' || !Object.hasOwn(specs, name)) { unclassified = true; continue }
    const spec = specs[name], args = isObject(action.args) ? action.args : {}
    const group: ResultGroup = {
      id: step, kind: spec.kind, title: spec.title,
      scope: typeof args.company_name === 'string' ? args.company_name : typeof args.project_number === 'string' ? args.project_number : '',
      fields: spec.fields.map(([key, label]) => ({ key, label })), rows: [], outcome: 'unavailable',
    }
    const rows = records.filter(r => isObject(r) && r.step_id === step)
    if (action.outcome === 'policy_rejection') group.outcome = 'rejected'
    else if (action.ok === false) group.outcome = 'failed'
    else if (action.ok === true) {
      const ids = new Map<string, number>()
      for (const row of rows) if (isObject(row) && typeof row.record_ref === 'string') ids.set(row.record_ref, (ids.get(row.record_ref) ?? 0) + 1)
      for (const row of rows) {
        if (!isObject(row) || typeof row.record_ref !== 'string' || !row.record_ref || ids.get(row.record_ref) !== 1) { unclassified = true; continue }
        handled.add(row)
        group.rows.push({ id: row.record_ref, values: Object.fromEntries(spec.fields.map(([key]) => [key, cellText(row[key])])) })
      }
      if (group.rows.length) group.outcome = 'records'
      else if (!rows.length && action.empty === true && action.exact_scope === true) group.outcome = 'empty'
    }
    groups.push(group)
  }
  if (records.some(r => !handled.has(r))) unclassified = true
  const citations: Citation[] = [], citationIds = new Set<number>()
  for (const source of sources) {
    if (!isObject(source) || !Number.isInteger(source.context_index) || Number(source.context_index) < 1
      || typeof source.doc_name !== 'string' || typeof source.chapter !== 'string' || typeof source.text !== 'string'
      || !source.text.trim() || citationIds.has(Number(source.context_index))) throw new StreamProtocolError('引用格式不完整')
    citationIds.add(Number(source.context_index))
    citations.push({ index: Number(source.context_index), title: source.doc_name, chapter: source.chapter, text: source.text,
      chunkId: typeof source.chunk_id === 'string' ? source.chunk_id : undefined,
      chunkUid: typeof source.chunk_uid === 'string' ? source.chunk_uid : undefined })
  }
  return { answer: payload.answer, status: business.execution_status as BusinessStatus, records: [], citations, groups,
    notice: unclassified ? '部分记录的业务关联缺失或不唯一，未生成对应卡片或表格。请结合本轮正文核对。' : undefined }
}

// 后端已转义 HTML / Markdown。只还原为 React 文本，不解析或插入 HTML。
export function publishedText(text: string): string {
  const entities: Record<string, string> = { amp: '&', lt: '<', gt: '>', quot: '"', '#x27': "'", '#39': "'" }
  return text.replace(/\\([\\`*_{}\[\]()#+.!|>~])/g, '$1').replace(/&(amp|lt|gt|quot|#x27|#39);/g, (_, name: string) => entities[name])
}
