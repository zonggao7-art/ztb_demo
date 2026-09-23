import { useEffect, useRef, useState } from 'react'
import type { FormEvent, ReactNode } from 'react'
import Icon from './Icon'
import { examples, scenarios, stages } from './demo'
import { liveExamples } from './examples'
import { isBusy } from './state'
import type { Scenario, Turn } from './types'
import { useChat } from './useChat'
import BusinessResults from './BusinessResults'
import { publishedText } from './api/results'
import s from './App.module.css'

function TextWithCitations({ text, turnId, available }: { text: string; turnId: string; available: number[] }) {
  const parts: ReactNode[] = text.split(/(【来源\d+】)/g).map((part, i) => {
    const match = /^【来源(\d+)】$/.exec(part)
    if (!match) return part
    if (!available.includes(Number(match[1]))) return <span key={i} className={s.inlineCitation} title="引用详情尚未收到">{match[1]}</span>
    return <button key={i} className={s.inlineCitation} aria-label={`展开来源${match[1]}`} onClick={() => {
      const el = document.getElementById(`${turnId}-citation-${match[1]}`) as HTMLDetailsElement | null
      if (el) { el.open = true; el.scrollIntoView({ behavior: 'smooth', block: 'nearest' }); el.querySelector('summary')?.focus() }
    }}>{match[1]}</button>
  })
  return <div className={s.answerText}>{parts}</div>
}

function Message({ turn, busy, onRetry }: { turn: Turn; busy: boolean; onRetry: () => void }) {
  const [copyStatus, setCopyStatus] = useState('')
  const running = isBusy(turn.status)
  const live = turn.mode === 'live'
  const answer = live ? publishedText(turn.answer) : turn.answer
  const statusLabels = { complete: '本轮结果已返回', clarify: '需要补充条件', partial: '部分完成', unsupported: '暂不支持此请求', insufficient_evidence: '证据不足', failed: '本轮未完成', cancelled: '已停止', interrupted: '结果接收中断', running: '处理中', streaming: '正在接收正文' }
  const copy = async () => {
    try { await navigator.clipboard.writeText(answer); setCopyStatus('已复制') }
    catch { setCopyStatus('复制失败，请选择正文复制') }
  }
  return <article className={s.turn}>
    <div className={s.userRow}><div className={s.userBubble}>{turn.question}</div><span className={s.userAvatar}>我</span></div>
    <div className={s.assistantRow}>
      <span className={s.assistantAvatar}><Icon name="logo" size={22} /></span>
      <div className={s.assistantContent}>
        <div className={s.assistantHeading}><strong>招投标智能助手</strong><span>{live ? '真实问答' : '演示回答'}</span></div>
        <div className={`${s.progress} ${running ? s.progressActive : ''}`} role="status">
          {running ? <span className={s.spinner} /> : <Icon name={turn.status === 'complete' ? 'check' : 'info'} size={15} />}
          <span>{running ? turn.stageLabel ?? stages[turn.stage] : statusLabels[turn.status]}</span>
          <span className={s.elapsed}>{turn.elapsed.toFixed(1)} 秒{!running && (live ? ' · 本次耗时' : ' · 演示耗时')}</span>
        </div>
        {running && !turn.answer && <div className={s.waiting}><span /><span /><span /><p>正在准备可核验的回答</p></div>}
        {answer && <TextWithCitations text={answer} turnId={turn.id} available={turn.citations.map(c => c.index)} />}
        {!!turn.groups?.length && <BusinessResults groups={turn.groups} demo={!live} />}
        {!!turn.records.length && <section className={s.resultSection} aria-label="中标记录">
          <div className={s.sectionHeading}><span><Icon name="chart" size={17} />中标记录</span><span className={s.count}>{turn.records.length} 条 · 虚构数据</span></div>
          <div className={s.tableScroll} tabIndex={0} role="region" aria-label="中标记录表格，可横向滚动"><table>
            <thead><tr><th>项目 / 编号</th><th>中标供应商</th><th>金额原始值</th><th>中标日期</th></tr></thead>
            <tbody>{turn.records.map(row => <tr key={row.id}><td><strong>{row.name}</strong><span>{row.id}</span></td><td>{row.supplier}</td><td className={s.amount}>{row.amount}</td><td>{row.date}</td></tr>)}</tbody>
          </table></div>
          <p className={s.tableNote}>金额单位未核定，保留原始值，不作换算或比较。</p>
        </section>}
        {!!turn.citations.length && <section className={s.resultSection} aria-label="法规引用">
          <div className={s.sectionHeading}><span><Icon name="book" size={17} />参考来源</span><span className={s.count}>{turn.citations.length} 条{!live && ' · 虚构引用'}</span></div>
          <div className={s.citations}>{turn.citations.map(c => <details key={c.index} id={`${turn.id}-citation-${c.index}`} className={s.citation}>
            <summary><span className={s.citationNumber}>{c.index}</span><span className={s.citationTitle}><strong>{c.title}</strong><small>{c.chapter}</small></span><Icon name="chevron" size={16} className={s.detailChevron} /></summary>
            <div className={s.citationBody}><span>{live ? '来源原文片段' : '原文预览 · 教学占位'}</span><p>{c.text}</p>{c.chunkUid && <small className={s.sourceId}>片段标识：{c.chunkUid}</small>}</div>
          </details>)}</div>
        </section>}
        {turn.notice && <div className={s.notice} role="status"><Icon name="info" size={18} /><span>{turn.notice}</span></div>}
        {!running && <div className={s.messageActions}>
          <span className={`${s.outcome} ${turn.status !== 'complete' ? s.outcomeMuted : ''}`}><Icon name={turn.status === 'complete' ? 'check' : 'info'} size={14} />{statusLabels[turn.status]}</span>
          <div>{turn.answer && <button onClick={copy}><Icon name="copy" size={15} />{copyStatus === '已复制' ? copyStatus : '复制'}</button>}<button disabled={busy} onClick={onRetry}><Icon name="retry" size={15} />重试</button></div>
          <span className={s.srOnly} role="status">{copyStatus}</span>
        </div>}
      </div>
    </div>
  </article>
}

export default function App() {
  const { state, dispatch, conversation, busy, start, cancel, newConversation, selectConversation, mode, changeMode, saveStatus, clearHistory } = useChat()
  const live = mode === 'live'
  const shownExamples = live ? liveExamples : examples.map(e => ({ ...e, id: e.scenario }))
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [aboutOpen, setAboutOpen] = useState(false)
  const [clearOpen, setClearOpen] = useState(false)
  const saveMessages = {
    saved: '已保存到此浏览器',
    unavailable: '本次更改未能保存到浏览器，可能是存储空间已满或保存被禁用。请先保留需要的内容。',
    invalid: '已有本地记录无法读取，原记录尚未覆盖。当前更改暂未保存，可清空记录后重新开始。',
    conflict: '另一个页面更新了会话记录，本页已暂停保存以免覆盖。请先复制本页需要的内容，再刷新恢复最新记录。',
  }
  const input = useRef<HTMLTextAreaElement>(null)
  const scroll = useRef<HTMLDivElement>(null)
  const follow = useRef(true)
  const lastTurn = conversation.turns.at(-1)
  useEffect(() => {
    if (!scroll.current) return
    if (!conversation.turns.length) scroll.current.scrollTop = 0
    else if (follow.current) scroll.current.scrollTop = scroll.current.scrollHeight
  }, [conversation.id, conversation.turns])
  useEffect(() => {
    const onEscape = (e: KeyboardEvent) => { if (e.key === 'Escape') { setSidebarOpen(false); setAboutOpen(false); setClearOpen(false) } }
    document.addEventListener('keydown', onEscape)
    return () => document.removeEventListener('keydown', onEscape)
  }, [])
  useEffect(() => {
    if (!aboutOpen && !sidebarOpen && !clearOpen) return
    const previous = document.activeElement as HTMLElement | null
    const container = document.querySelector<HTMLElement>(aboutOpen || clearOpen ? '[role="dialog"]' : 'aside')
    const getFocusable = () => Array.from(container?.querySelectorAll<HTMLElement>('button:not(:disabled), a[href], select') ?? [])
    getFocusable()[0]?.focus()
    const trap = (e: KeyboardEvent) => {
      if (e.key !== 'Tab') return
      const items = getFocusable(), first = items[0], last = items.at(-1)
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last?.focus() }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first?.focus() }
    }
    document.addEventListener('keydown', trap)
    return () => { document.removeEventListener('keydown', trap); previous?.focus() }
  }, [aboutOpen, sidebarOpen, clearOpen])
  const send = (e?: FormEvent) => { e?.preventDefault(); follow.current = true; start(state.draft, state.scenario) }
  const chooseExample = (question: string, scenario: Scenario) => {
    dispatch({ type: 'draft', value: question }); dispatch({ type: 'scenario', value: scenario }); input.current?.focus()
  }
  const reset = () => { newConversation(); follow.current = true; setSidebarOpen(false); input.current?.focus() }
  return <div className={s.app}>
    {sidebarOpen && <button className={s.sidebarBackdrop} aria-label="关闭会话菜单" onClick={() => setSidebarOpen(false)} />}
    <aside className={`${s.sidebar} ${sidebarOpen ? s.sidebarOpen : ''}`} aria-label="会话导航">
      <a className={s.brand} href="#" onClick={e => { e.preventDefault(); reset() }}><span className={s.brandIcon}><Icon name="logo" size={27} /></span><span><strong>招投标智能助手</strong><small>BIDDING ASSISTANT</small></span></a>
      <button className={s.newChat} onClick={reset}><Icon name="plus" size={18} /><span>新建会话</span><span className={s.newBadge}>＋</span></button>
      <div className={s.navLabel}>工作空间</div>
      <div className={s.currentNav}><Icon name="chat" size={19} /><span>智能问答</span><span className={s.navDot} /></div>
      <div className={s.historyHeading}><span>会话记录 · {state.conversations.length.toString().padStart(2, '0')}</span><button disabled={saveStatus === 'conflict'} onClick={() => { setClearOpen(true); setSidebarOpen(false) }}>清空记录</button></div>
      <nav className={s.history}>{state.conversations.map(c => <button key={c.id} aria-current={state.activeId === c.id ? 'page' : undefined} className={state.activeId === c.id ? s.selectedHistory : ''} onClick={() => { selectConversation(c.id); follow.current = true; setSidebarOpen(false) }}><Icon name="chat" size={15} /><span>{c.mode === 'demo' ? '〔演示〕' : ''}{c.title}</span></button>)}</nav>
      <div className={s.sidebarBottom}>
        <div className={s.scopeCard}><Icon name="shield" size={21} /><strong>有据可依，审慎判断</strong><p>答案结合来源阅读，查询结果作为核验线索。</p><button onClick={() => { setAboutOpen(true); setSidebarOpen(false) }}>了解能力边界 <Icon name="arrow" size={14} /></button></div>
        <div className={s.profile}><span className={s.profileAvatar}>访</span><span><strong>作品集访客</strong><small>本地体验空间</small></span><span className={s.profileDot} /></div>
      </div>
    </aside>

    <main className={s.main}>
      <header className={s.header}>
        <div className={s.headerLeft}><button className={s.menuButton} aria-label="打开会话菜单" aria-expanded={sidebarOpen} onClick={() => setSidebarOpen(true)}><Icon name="menu" /></button><span className={s.breadcrumb}>工作台</span><span className={s.slash}>/</span><strong>智能问答</strong></div>
        <div className={s.headerRight}><span className={s.demoBadge}><span />{live ? '真实问答' : '界面演示'}</span><button className={s.iconButton} aria-label="查看使用说明" onClick={() => setAboutOpen(true)}><Icon name="info" size={19} /></button></div>
      </header>
      <div className={`${s.demoToolbar} ${live ? s.liveToolbar : ''}`}>
        <span><Icon name="spark" size={14} /><span>{live ? '每轮独立处理，请完整描述本轮问题' : '所有内容均为虚构示例'}</span></span>
        <div className={s.toolbarControls}>
          {!live && <label>演示场景<select aria-label="演示场景" value={state.scenario} disabled={busy} onChange={e => dispatch({ type: 'scenario', value: e.target.value as Scenario })}>{scenarios.map(x => <option key={x.value} value={x.value}>{x.label}</option>)}</select></label>}
          <div className={s.modeSwitch} aria-label="问答模式"><button aria-pressed={live} disabled={busy} onClick={() => changeMode('live')}>真实问答</button><button aria-pressed={!live} disabled={busy} onClick={() => changeMode('demo')}>界面演示</button></div>
        </div>
      </div>
      <div className={s.scrollArea} ref={scroll} onScroll={() => { if (scroll.current) { const e = scroll.current; follow.current = e.scrollHeight - e.scrollTop - e.clientHeight < 110 } }}>
        {!conversation.turns.length ? <div className={`${s.welcome} ${live ? s.moduleWelcome : ''}`}>
          <div className={s.welcomeEyebrow}><span />让信息成为可靠的依据</div>
          <h1>招采问题，<br />从这里<span>找到线索。</span></h1>
          <p className={s.welcomeDescription}>连接法规知识与业务记录。<br className={s.mobileBreak} />让每一次查询有来源，每一条结果可核验。</p>
          <div className={s.exampleGrid} aria-label={live ? "六个功能模块" : "虚构演示示例"}>{shownExamples.map((e, i) => <button className={s.exampleCard} key={e.id} onClick={() => chooseExample(e.question, e.scenario)}><div className={s.cardTop}><span className={s.exampleIcon}><Icon name={e.icon} size={23} /></span><span className={s.cardIndex}>0{i + 1}</span></div><strong>{e.title}</strong><p>{e.detail}</p><div className={s.exampleQuestion}><span>{e.question}</span><Icon name="arrow" size={17} /></div></button>)}</div>
          <div className={s.welcomeFoot}><span><Icon name="shield" size={15} />保留来源</span><i /><span><Icon name="chart" size={15} />结构化呈现</span><i /><span><Icon name="check" size={15} />明确能力边界</span></div>
        </div> : <div className={s.conversation}>
          <div className={s.conversationIntro}><span>本次对话</span><span>{live ? '每轮独立' : '虚构示例'} · {saveStatus === 'saved' ? '浏览器本地保存' : '本次更改未保存'}</span></div>
          {conversation.turns.map(turn => <Message key={turn.id} turn={turn} busy={busy} onRetry={() => { follow.current = true; start(turn.question, turn.mode !== 'live' && ['error', 'timeout', 'disconnect'].includes(turn.scenario) ? 'knowledge' : turn.scenario, false) }} />)}
        </div>}
      </div>
      <div className={s.composerDock}>
        {saveStatus !== 'saved' && <div className={s.storageNotice} role="status"><Icon name="info" size={16} /><span>{saveMessages[saveStatus]}</span></div>}
        <form className={`${s.composer} ${busy ? s.composerBusy : ''}`} onSubmit={send}>
          <label className={s.srOnly} htmlFor="question">输入问题</label>
          <textarea id="question" ref={input} value={state.draft} onChange={e => dispatch({ type: 'draft', value: e.target.value })} placeholder={busy ? '可以先写下一条问题，当前回答结束后发送…' : '问一个招采问题，或输入项目编号开始查询…'} maxLength={4000} rows={2} onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing && e.nativeEvent.keyCode !== 229) { e.preventDefault(); if (!busy) send() } }} />
          <div className={s.composerBottom}><span className={s.inputHint}><Icon name="book" size={15} />{live ? '请完整描述本轮问题' : scenarios.find(x => x.value === state.scenario)?.label}<span className={s.keyboardHint}>Enter 发送 · Shift + Enter 换行</span></span><div className={s.sendControls}>{busy && <button type="button" className={s.stopButton} onClick={cancel}><Icon name="stop" size={13} />停止</button>}<button type="submit" className={s.sendButton} disabled={busy || !state.draft.trim()} aria-label="发送问题"><Icon name="up" size={20} /></button></div></div>
        </form>
        <div className={s.composerFoot}><span>{live ? '回答供核验参考，请结合原文和来源判断。' : '静态演示，不发送真实查询。示例内容不作为业务依据。'}</span><span className={s.draftHint}>{saveStatus !== 'saved' ? '本次更改未保存' : busy && state.draft ? '草稿已保存到此浏览器' : lastTurn ? saveMessages.saved : '会话将在此浏览器保存'}</span></div>
      </div>
    </main>
    {clearOpen && <div className={s.modalBackdrop} onClick={e => { if (e.target === e.currentTarget) setClearOpen(false) }}><section className={s.modal} role="dialog" aria-modal="true" aria-labelledby="clear-title">
      <button className={s.modalClose} aria-label="取消清空" onClick={() => setClearOpen(false)}><Icon name="close" /></button>
      <h2 id="clear-title">清空此浏览器的会话记录？</h2>
      <p>将清除已保存的真实问答、演示会话和草稿，无法恢复。正在进行的请求也会停止。</p>
      <p>只清理本页面保存的记录，不删除业务数据库或服务端日志。</p>
      <div className={s.confirmActions}><button onClick={() => setClearOpen(false)}>保留记录</button><button className={s.dangerButton} disabled={saveStatus === 'conflict'} onClick={() => { if (clearHistory()) { setClearOpen(false); follow.current = true; input.current?.focus() } }}>确认清空</button></div>
      {saveStatus !== 'saved' && <p role="status">{saveMessages[saveStatus]}</p>}
    </section></div>}
    {aboutOpen && <div className={s.modalBackdrop} onClick={e => { if (e.target === e.currentTarget) setAboutOpen(false) }}><section className={s.modal} role="dialog" aria-modal="true" aria-labelledby="about-title"><button autoFocus className={s.modalClose} aria-label="关闭说明" onClick={() => setAboutOpen(false)}><Icon name="close" /></button><span className={s.modalIcon}><Icon name="shield" size={29} /></span><h2 id="about-title">有据可依，也有明确边界</h2><p>{live ? '真实问答会将本轮问题发送给本地 Agent，并调用已配置的模型服务和数据工具。目前每轮独立处理，请在每次提问中提供完整条件。' : '这是招投标智能助手的作品集界面演示。所有回答、企业、项目和引用均为虚构示例，输入不会发送给真实 Agent。'}</p><p>真实业务结果需要结合原始材料核验，不自动认定违法、无风险或具备投标资格。</p><div className={s.modalNote}>界面演示可体验虚构回答和异常状态；切换模式会创建新会话。会话和草稿保存在此浏览器；刷新或重新打开可恢复，清除浏览器数据后会丢失。共享设备上的其他使用者也能查看这些本地记录。保存历史不改变每轮独立处理的方式。收到结果不等于结果必然满足需求，仍需你核验。</div><button className={s.modalDone} onClick={() => setAboutOpen(false)}>了解，开始探索 <Icon name="arrow" size={16} /></button></section></div>}
  </div>
}
