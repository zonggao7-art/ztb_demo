// 页面模型；后端 envelope 的读取与转换在 api/ 中。
export type Mode = 'live' | 'demo'
export type Scenario = 'knowledge' | 'award' | 'business' | 'general' | 'clarify' | 'partial' | 'error' | 'timeout' | 'disconnect'
export type BusinessStatus = 'complete' | 'clarify' | 'partial' | 'unsupported' | 'insufficient_evidence'
export type RunStatus = 'running' | 'streaming' | BusinessStatus | 'failed' | 'cancelled' | 'interrupted'
export interface Citation { index: number; title: string; chapter: string; text: string; chunkId?: string; chunkUid?: string }
export interface Award { id: string; name: string; supplier: string; amount: string; date: string }
export interface ResultGroup {
  id: string
  kind: 'company' | 'scope' | 'penalty' | 'award'
  title: string
  scope: string
  fields: { key: string; label: string }[]
  rows: { id: string; values: Record<string, string> }[]
  outcome: 'records' | 'empty' | 'failed' | 'unavailable' | 'rejected'
}
export interface Result { answer: string; citations: Citation[]; records: Award[]; status: BusinessStatus; groups?: ResultGroup[]; notice?: string }
export interface Turn {
  id: string
  question: string
  scenario: Scenario
  status: RunStatus
  stage: number
  answer: string
  citations: Citation[]
  records: Award[]
  elapsed: number
  notice?: string
  mode?: Mode
  serverRequestId?: string
  stageLabel?: string
  groups?: ResultGroup[]
}
export interface Conversation { id: string; title: string; turns: Turn[]; mode?: Mode }
