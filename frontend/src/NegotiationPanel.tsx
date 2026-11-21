import { useId, useState } from 'react'
import { ArrowRight, ChevronDown, MessagesSquare } from 'lucide-react'
import {
  METERS, TRACK_INFO, activeMeter, type CollaborationMode, type NegotiationFeedEvent, type State,
  type TimeSignature,
} from './model'

const modes: Record<CollaborationMode, string> = {
  solo: '独立',
  shared: '共享状态',
  negotiate: '显式协商',
}

const stages: Record<NegotiationFeedEvent['stage'], string> = {
  proposal: '提案',
  accepted: '接受',
  declined: '拒绝',
  timeout: '超时',
}

const proposals: Record<NegotiationFeedEvent['kind'], string> = {
  kick_pattern: '底鼓对齐',
  reserve_space: '伴奏留空',
  lighter_drums: '鼓组留白',
}

const beatSuffix: Record<number, string> = {
  0: '', 3: '¼', 4: '⅓', 6: '½', 8: '⅔', 9: '¾',
}

function beatLabel(tick: number, meter: TimeSignature) {
  const beatLength = METERS[meter].pulseTicks
  const beat = Math.floor(tick / beatLength) + 1
  const fraction = tick % beatLength / beatLength
  const twelfths = fraction * 12
  return twelfths in beatSuffix ? `${beat}${beatSuffix[twelfths]}` : (beat + fraction).toFixed(2)
}

function Exchange({ event, meter }: { event: NegotiationFeedEvent; meter: TimeSignature }) {
  const proposer = TRACK_INFO[event.sender]
  const respondent = TRACK_INFO[event.recipient]
  const [from, to] = event.stage === 'proposal' ? [proposer, respondent] : [respondent, proposer]
  const signature = event.time_signature ?? meter

  return (
    <li className="exchange">
      <span className={`exchange-stage ${event.stage}`}>{stages[event.stage]}</span>
      <span className="exchange-direction">
        <strong style={{ color: from.color }}>{from.name}</strong>
        {event.stage === 'timeout' ? (
          <span>未回应</span>
        ) : (
          <>
            <ArrowRight size={13} aria-label="发给" />
            <strong style={{ color: to.color }}>{to.name}</strong>
          </>
        )}
      </span>
      <span className="exchange-content">
        <span>{proposals[event.kind]}</span>
        {event.detail && <span className="exchange-detail">{event.detail}</span>}
        {event.stage === 'proposal' && event.kind === 'kick_pattern' && event.ticks.length > 0 && (
          <span className="exchange-ticks" title={`小节内的${signature === '6/8' ? '附点四分' : '四分'}拍位置`}>
            落点 {event.ticks.map(tick => beatLabel(tick, signature)).join(' / ')}
          </span>
        )}
      </span>
    </li>
  )
}

export function NegotiationPanel({ state, disabled, onModeChange, alwaysOpen = false }: {
  state: State
  disabled: boolean
  onModeChange: (mode: CollaborationMode) => void
  alwaysOpen?: boolean
}) {
  const feedId = useId()
  const [toggled, setExpanded] = useState(false)
  const expanded = alwaysOpen || toggled
  const [selectedBar, setSelectedBar] = useState<number | null>(null)
  const pending = state.pending_changes?.at(-1) ?? state.pending
  const activeMode = state.settings.collaboration ?? 'negotiate'
  const desiredMode = pending?.settings.collaboration ?? activeMode
  const events = state.negotiation ?? []
  const newest = events.at(-1)
  const byBar = new Map<number, NegotiationFeedEvent[]>()
  for (const event of events) {
    const list = byBar.get(event.target_bar)
    if (list) list.push(event)
    else byBar.set(event.target_bar, [event])
  }
  const bars = [...new Set(events.map(event => event.target_bar).reverse())].slice(0, 5)
  const bar = selectedBar !== null && bars.includes(selectedBar) ? selectedBar : (bars[0] ?? null)
  const messages = bar === null ? [] : byBar.get(bar) ?? []
  const timeline = state.pending_changes ?? (state.pending ? [state.pending] : [])
  const messageMeter = timeline.findLast(change => bar !== null && change.bar <= bar)?.settings.meter
    ?? activeMeter(state)

  return (
    <section className="panel negotiation" aria-label="乐手协商">
      <header className="panel-heading negotiation-heading">
        <h2>
          {alwaysOpen ? <><MessagesSquare size={17} aria-hidden="true" />乐手协商</> : <button
            type="button"
            className="negotiation-toggle"
            aria-expanded={expanded}
            aria-controls={feedId}
            onClick={() => setExpanded(value => !value)}
          >
            <MessagesSquare size={17} aria-hidden="true" />
            乐手协商
            <ChevronDown size={14} className="negotiation-chevron" aria-hidden="true" />
          </button>}
        </h2>
        {newest && activeMode === 'negotiate' && (
          <span className="negotiation-latest">
            第 {newest.target_bar + 1} 小节 · {stages[newest.stage]}
          </span>
        )}
        <label className="collaboration-mode">
          <span>协作模式</span>
          <select
            aria-label="协作模式"
            value={desiredMode}
            disabled={disabled}
            onChange={event => onModeChange(event.target.value as CollaborationMode)}
          >
            {Object.entries(modes).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
        </label>
      </header>
      <div className="negotiation-body" id={feedId} hidden={!expanded}>
        {bars.length > 0 ? (
          <>
            <div className="negotiation-bars" role="group" aria-label="协商小节">
              {bars.map((value, index) => (
                <button
                  type="button"
                  key={value}
                  aria-pressed={value === bar}
                  onClick={() => setSelectedBar(index === 0 ? null : value)}
                >
                  第 {value + 1} 小节
                </button>
              ))}
            </div>
            <ol className="negotiation-feed" aria-label={`第 ${(bar ?? 0) + 1} 小节的协商记录`}>
              {messages.map(event => <Exchange key={event.sequence} event={event} meter={messageMeter} />)}
            </ol>
          </>
        ) : (
          <p className="negotiation-empty">{activeMode === 'negotiate' ? '等待乐手提案' : '切到显式协商后会收到提案和回应'}</p>
        )}
      </div>
    </section>
  )
}
