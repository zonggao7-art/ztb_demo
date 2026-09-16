import type { ResultGroup } from './types'
import Icon from './Icon'
import s from './App.module.css'

const outcomes = {
  empty: '精确查询成功，本系统暂未收录匹配记录；不代表现实中不存在。',
  failed: '这项查询未成功返回，无法判断是否存在记录。',
  rejected: '这项查询申请未通过程序校验，没有据此取得查询结果。',
  unavailable: '本轮未提供可展示的完整记录，不能据此判断没有匹配数据。',
}
export default function BusinessResults({ groups, demo = false }: { groups: ResultGroup[]; demo?: boolean }) {
  return groups.map(group => <section className={s.resultSection} aria-label={group.title} key={group.id}>
    <div className={s.sectionHeading}><span><Icon name={group.kind === 'company' || group.kind === 'scope' ? 'logo' : 'chart'} size={17} />{group.title}</span><span className={s.count}>{group.rows.length ? `${group.rows.length} 条记录` : '查询结果'}{demo && ' · 虚构数据'}</span></div>
    {group.scope && <p className={s.groupScope}>{group.scope}</p>}
    {group.outcome !== 'records' ? <div className={s.notice}><Icon name="info" size={17} /><span>{outcomes[group.outcome]}</span></div>
      : group.kind === 'company' || group.kind === 'scope' ? <div className={s.companyCards}>{group.rows.map(row => <dl key={row.id} className={`${s.companyCard} ${group.kind === 'scope' ? s.scopeFields : ''}`}>
        {group.fields.map(field => <div key={field.key}><dt>{field.label}</dt><dd>{row.values[field.key]}</dd></div>)}
      </dl>)}</div>
      : <div className={s.tableScroll} role="region" tabIndex={0} aria-label={`${group.title}表格，可横向滚动`}><table className={group.kind === 'penalty' ? s.penaltyTable : undefined}>
        <thead><tr>{group.fields.map(field => <th key={field.key}>{field.label}</th>)}</tr></thead>
        <tbody>{group.rows.map(row => <tr key={row.id}>{group.fields.map(field => <td key={field.key} className={field.key === 'winning_amount' ? s.amount : undefined}>{row.values[field.key]}</td>)}</tr>)}</tbody>
      </table></div>}
    {group.kind === 'award' && group.rows.length > 0 && <p className={s.tableNote}>金额保留原始值，单位请结合正文说明核对；不作换算或比较。以上为本次检索样本。</p>}
  </section>)
}
