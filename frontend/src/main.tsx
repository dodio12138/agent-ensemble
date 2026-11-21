import { createRoot } from 'react-dom/client'
import { useEffect, useState, type CSSProperties } from 'react'
import {
  Activity, AudioLines, ChevronsUpDown, CircleAlert, Download, Drum, FileMusic, Gamepad2, Guitar, KeyboardMusic, Link2,
  ListMusic, Megaphone, MessagesSquare, Music, Music2, Pause, Piano, Play, Repeat2, Square, Volume2, VolumeX, Wand2,
  Waves, Zap,
  type LucideIcon,
} from 'lucide-react'
import {
  GENRE_NAMES, GROOVE_NAMES, LINEUPS, LINEUP_NAMES, LINEUP_SUBTITLES, METERS, SECTION_NAMES, TICKS_PER_QUARTER, TRACK_INFO,
  activeMeter, pitchName, songPosition, trackLabel, tracksFor, type GrooveTransition, type Lineup, type SoundNote,
  type State, type TimeSignature, type TrackId,
} from './model'
import { AudioRoute } from './AudioRoute'
import { ConductorAgentSwitch } from './ConductorAgentSwitch'
import { ConductorPanel } from './ConductorPanel'
import { MidiPanel } from './MidiPanel'
import { MidiScorePlayer } from './MidiScorePlayer'
import { Range } from './Range'
import { NegotiationPanel } from './NegotiationPanel'
import { SyncPanel } from './SyncPanel'
import { useSession } from './session'
import './styles.css'

const decimalBar = (bar: number) => String(bar + 1).padStart(2, '0')
const drumNames: Record<number, string> = {
  36: 'Kick', 37: 'Side stick', 38: 'Snare', 42: 'Hi-hat', 44: 'Pedal',
  45: 'Low tom', 46: 'Open hat', 47: 'Mid tom', 49: 'Crash', 51: 'Ride',
}
const sounds: Record<TrackId, Record<number, string>> = {
  drums: { 0: '标准鼓组' },
  bass: { 32: '原声贝斯', 33: '电贝斯 · 指弹', 34: '电贝斯 · 拨片', 35: '无品贝斯', 38: '合成贝斯' },
  piano: {
    0: '三角钢琴', 1: '明亮钢琴', 2: '电声大钢琴', 4: '电钢琴', 11: '颤音琴', 19: '管风琴',
    46: '竖琴', 8: '钢片琴', 80: '方波 8-bit',
  },
  rhythm_guitar: { 27: '清音电吉他', 29: '过载电吉他', 30: '失真电吉他', 25: '钢弦原声吉他' },
  lead_guitar: { 30: '失真电吉他', 29: '过载电吉他', 27: '清音电吉他' },
  synth: { 89: '暖色 Pad', 48: '弦乐合奏', 21: '手风琴', 61: '铜管组' },
  strings: { 48: '弦乐合奏', 49: '慢弦乐', 45: '拨弦', 44: '震音弦乐', 50: '合成弦乐' },
  brass: { 60: '圆号', 61: '铜管组', 56: '小号', 57: '长号', 62: '合成铜管' },
  melody: { 73: '长笛', 75: '排箫', 71: '单簧管', 68: '双簧管', 60: '圆号', 56: '小号', 80: '方波 8-bit', 81: '锯齿波' },
  chip: { 80: '方波 8-bit', 81: '锯齿波', 8: '钢片琴', 9: '钟琴', 10: '八音盒', 11: '颤音琴', 46: '竖琴' },
}
const icons = {
  drums: Drum, bass: Guitar, piano: Piano, rhythm_guitar: Guitar, lead_guitar: Zap, synth: Waves,
  strings: Music2, brass: Megaphone, melody: Music, chip: Gamepad2,
}
const defaultProgression = ['Dm7', 'G7', 'Cmaj7', 'Am7', 'Dm7', 'G7', 'Em7', 'A7']

type DockTab = 'conductor' | 'keyboard' | 'score' | 'negotiation' | 'sync'
const DOCK_TABS: { id: DockTab; label: string; icon: LucideIcon }[] = [
  { id: 'conductor', label: '指挥', icon: Wand2 },
  { id: 'keyboard', label: '键盘', icon: KeyboardMusic },
  { id: 'score', label: '原谱', icon: FileMusic },
  { id: 'negotiation', label: '协商', icon: MessagesSquare },
  { id: 'sync', label: '同步', icon: Link2 },
]
const DOCK_KEY = 'agent-ensemble:dock-tab'

function storedTab(): DockTab {
  try {
    const value = localStorage.getItem(DOCK_KEY)
    if (DOCK_TABS.some(tab => tab.id === value)) return value as DockTab
  } catch {
    // Storage can be unavailable in private windows; the default tab is fine.
  }
  return 'conductor'
}

function useDockTab() {
  const [tab, setTab] = useState<DockTab>(storedTab)
  function choose(next: DockTab) {
    setTab(next)
    try {
      localStorage.setItem(DOCK_KEY, next)
    } catch {
      // Remembering the tab is only a convenience.
    }
  }
  return [tab, choose] as const
}

function transitionLabel(transition: GrooveTransition) {
  const changes = []
  const from = transition.from_genre ?? transition.from_style
  const to = transition.to_genre ?? transition.to_style
  if (from !== to) {
    changes.push(`${GENRE_NAMES[from]} → ${GENRE_NAMES[to]}`)
  } else if (transition.to_groove && transition.from_groove !== transition.to_groove) {
    const target = GROOVE_NAMES[transition.to_groove]
    changes.push(transition.from_groove ? `${GROOVE_NAMES[transition.from_groove]} → ${target}` : `切换为${target}`)
  }
  if (transition.from_bpm !== transition.to_bpm) {
    changes.push(`${transition.from_bpm} → ${transition.to_bpm} BPM`)
  }
  const fromMeter = transition.from_meter ?? '4/4'
  const toMeter = transition.to_meter ?? '4/4'
  if (fromMeter !== toMeter) changes.push(`${fromMeter} → ${toMeter} 拍`)
  return changes.join(' · ') || '调整演奏律动'
}

function accent(track: TrackId): CSSProperties {
  return { '--accent': TRACK_INFO[track].color } as CSSProperties
}

function TrackIcon({ track, className }: { track: TrackId; className?: string }) {
  const Icon = icons[track]
  return <span className={className} style={accent(track)}><Icon size={18} strokeWidth={1.8} /></span>
}

function laneLabels(track: TrackId) {
  if (track === 'drums') return ['Ride', 'Kick']
  const [low, high] = TRACK_INFO[track].range
  return [pitchName(high), pitchName(low)]
}

function LineupSwitch({ selected, pending, disabled, onChange }: {
  selected: Lineup
  pending?: { bar: number; lineup: Lineup }
  disabled: boolean
  onChange: (lineup: Lineup) => void
}) {
  return (
    <div className="lineup-control">
      <div className="lineup-options" role="group" aria-label="乐队编制">
        {LINEUPS.map(lineup => (
          <button type="button" key={lineup} disabled={disabled} aria-pressed={lineup === selected}
            onClick={() => onChange(lineup)}>{LINEUP_NAMES[lineup]}</button>
        ))}
      </div>
      {pending && (
        <span className="lineup-pending" role="status">{LINEUP_NAMES[pending.lineup]}于第 {pending.bar + 1} 小节生效</span>
      )}
    </div>
  )
}

function Heading({ icon: Icon, title, children }: { icon: LucideIcon; title: string; children?: React.ReactNode }) {
  return (
    <header className="panel-heading">
      <h2><Icon size={17} aria-hidden="true" />{title}</h2>
      {children}
    </header>
  )
}

function Editable({
  value, label, onCommit, number = false, disabled,
}: {
  value: string
  label: string
  onCommit: (value: string) => void
  number?: boolean
  disabled: boolean
}) {
  const [draft, setDraft] = useState(value)
  useEffect(() => setDraft(value), [value])
  function save() {
    const next = draft.trim()
    if (next && next !== value) onCommit(next)
    setDraft(value)
  }
  return (
    <input
      type={number ? 'number' : 'text'}
      min={number ? 40 : undefined}
      max={number ? 240 : undefined}
      maxLength={number ? undefined : 14}
      aria-label={label}
      disabled={disabled}
      value={draft}
      onFocus={event => event.target.select()}
      onChange={event => setDraft(event.target.value)}
      onBlur={save}
      onKeyDown={event => {
        if (event.key === 'Enter') event.currentTarget.blur()
        if (event.key === 'Escape') {
          setDraft(value)
          event.preventDefault()
        }
      }}
      spellCheck={false}
    />
  )
}

function PianoRoll({
  state, active, performed,
}: {
  state: State | null
  active: ReadonlyMap<string, SoundNote>
  performed: ReadonlyMap<string, number>
}) {
  const firstBar = state?.bar ?? 0
  const viewport = [firstBar, firstBar + 1, firstBar + 2]
  const progression = state?.settings.progression ?? defaultProgression
  const pending = state?.pending_changes ?? (state?.pending ? [state.pending] : [])
  const meter = activeMeter(state)
  const plans = state?.plans.filter(plan => viewport.includes(plan.bar)) ?? []
  let visibleTicks = 0
  const bars = viewport.map(bar => {
    const change = pending.findLast(change => bar >= change.bar)
    const settings = change?.settings ?? state?.settings
    const plan = plans.find(candidate => candidate.bar === bar)
    const song = settings?.song
    const songStart = change?.song_origin ?? state?.song_origin ?? state?.composition?.start_bar ?? 0
    const position = song ? songPosition(song, bar, songStart, settings.loop) : null
    const signature = bar === firstBar ? meter
      : plan?.time_signature ?? position?.section.meter ?? settings?.meter ?? meter
    const { numerator, pulseTicks } = METERS[signature]
    const barTicks = (bar === firstBar ? state?.bar_ticks : undefined) ?? METERS[signature].barTicks
    const entry = {
      bar, settings, plan, signature, numerator, position,
      barTicks, pulses: barTicks / pulseTicks, startTick: visibleTicks,
    }
    visibleTicks += barTicks
    return entry
  })
  const rollColumns = bars.map(({ barTicks }) => `minmax(0, ${barTicks}fr)`).join(' ')
  // Show the larger band whenever it plays anywhere in view, so its lanes never jump.
  const viewLineups = [
    state?.settings.lineup ?? 'trio',
    ...pending.filter(change => viewport.includes(change.bar)).map(change => change.settings.lineup ?? 'trio'),
  ]
  const laneLineup: Lineup = viewLineups.find(item => item !== 'trio') ?? 'trio'
  const lanes = tracksFor(laneLineup)
  const playback = state && state.transport !== 'stopped'
  return (
    <section className="panel score" aria-label="钢琴卷帘">
      <Heading icon={Activity} title="钢琴卷帘">
        <div className="note-legend"><span><i />计划音符</span><span><i />已触发</span></div>
      </Heading>
      <div className="score-body">
        <div className="roll-sidebar">
          <div className="roll-time">小节 / 拍</div>
          {lanes.map(track => {
            const { name, en } = trackLabel(track, laneLineup)
            const [high, low] = laneLabels(track)
            return (
              <div className="roll-track" key={track} style={accent(track)}>
                <div className="roll-track-title">
                  <TrackIcon track={track} className="roll-icon" />
                  <div><strong>{name}</strong><small>{en}</small></div>
                </div>
                <span className="lane-label top">{high}</span>
                <span className="lane-label bottom">{low}</span>
              </div>
            )
          })}
        </div>
        <div className="roll-display" style={{ '--roll-columns': rollColumns } as CSSProperties}>
          <div className="roll-bars">
            {bars.map(({ bar, settings, plan, signature, numerator, position }, index) => {
              const chords = settings?.progression ?? progression
              const endOfChorus = (Math.floor(firstBar / chords.length) + 1) * chords.length
              const visible = settings?.song ? position !== null : !settings || settings.loop || bar < endOfChorus
              const chord = visible ? plan?.harmony ?? (position
                ? position.section.progression[position.offset % position.section.progression.length]
                : chords[bar % chords.length]) : '—'
              const improvisation = visible && plan?.improvisation_phrase
              const scheduled = pending.find(change => change.transition && (change.bar === bar || change.pickup_bar === bar))
              const transition = visible ? (
                plan?.transition
                ?? (bar === firstBar ? state?.transition : undefined)
                ?? scheduled?.transition
              ) : undefined
              const phase = transition && (scheduled?.pickup_bar === bar ? 'pickup' : transition.phase)
              const mark = phase === 'pickup' ? '过门' : '起拍'
              return (
                <div key={bar} className={`roll-bar ${index === 0 ? 'current' : ''} ${phase ?? ''} ${improvisation ? 'improvised' : ''}`}
                  aria-label={`第 ${bar + 1} 小节 · ${signature} 拍`}>
                  <div className="roll-bar-caption">
                    <span>{decimalBar(bar)}</span><strong>{chord}</strong>
                    {signature !== '4/4' && <span className="roll-meter">{signature}</span>}
                    {transition && (
                      <span className="roll-transition" title={`${mark} · ${transitionLabel(transition)}`}>
                        {mark}
                      </span>
                    )}
                    {improvisation && !transition && (
                      <span className="roll-transition roll-improvisation" title={`${TRACK_INFO[improvisation.lead].name}领奏`}>即兴</span>
                    )}
                  </div>
                  <div className={`beats ${signature === '6/8' ? 'compound' : ''}`}
                    style={{ '--beats': numerator } as CSSProperties}
                    aria-label={signature === '6/8' ? '两大拍，每拍三个八分音符' : `${numerator} 拍`}>
                    {Array.from({ length: numerator }, (_, beat) => <span key={beat}>{beat + 1}</span>)}
                  </div>
                </div>
              )
            })}
          </div>
          {lanes.map(track => (
            <div key={track} className="roll-lane" style={accent(track)} aria-label={`${trackLabel(track, laneLineup).name}计划音符`}>
              <div className="roll-grid" aria-hidden="true">
                {bars.map(({ bar, numerator, pulses }) => (
                  <div key={bar} style={{ '--beats': numerator, '--pulses': pulses } as CSSProperties} />
                ))}
              </div>
              {plans.flatMap(plan => plan.notes.filter(note => note.track_id === track)).map(note => {
                const [min, max] = TRACK_INFO[track].range
                const level = Math.min(1, Math.max(0, (max - note.pitch) / (max - min)))
                const index = note.bar - firstBar
                const { barTicks, signature, startTick } = bars[index]
                if (note.start_tick >= barTicks) return null
                const offset = (startTick + note.start_tick) / visibleTicks
                const playing = active.has(note.event_id)
                const pitch = track === 'drums' ? drumNames[note.pitch] ?? pitchName(note.pitch) : pitchName(note.pitch)
                return (
                  <div
                    key={note.event_id}
                    className={`note ${playing ? 'fired' : performed.has(note.event_id) ? 'performed' : ''}`}
                    title={`${decimalBar(note.bar)} · ${signature} · ${pitch} · 力度 ${note.velocity}`}
                    style={{
                      left: `${offset * 100}%`,
                      width: `${Math.min(note.duration_ticks, barTicks - note.start_tick) / visibleTicks * 100}%`,
                      top: `${8 + level * 76}%`,
                    }}
                  />
                )
              })}
            </div>
          ))}
          {playback && (
            <div className="playhead" style={{ left: `${Math.min(state.tick, bars[0].barTicks) / visibleTicks * 100}%` }} />
          )}
        </div>
      </div>
      <footer className="score-footer">
        <span>{plans.length} 小节已规划</span>
        <span>{meter} <i /> {state?.ticks_per_quarter ?? TICKS_PER_QUARTER} PPQ</span>
      </footer>
    </section>
  )
}

function App() {
  const session = useSession()
  const [scoreOwnsAudio, setScoreOwnsAudio] = useState(false)
  const { state, connection, error, active, performed } = session
  const pending = state?.pending_changes ?? (state?.pending ? [state.pending] : [])
  const next = pending.at(-1)
  const stagedTransition = pending.find(change => change.transition)
  const settings = next?.settings ?? state?.settings
  const activeLineup = state?.settings.lineup ?? 'trio'
  const lineup = settings?.lineup ?? 'trio'
  const lineupTracks = tracksFor(lineup)
  const lineupChange = pending.findLast((change, index) =>
    (change.settings.lineup ?? 'trio') !== (pending[index - 1]?.settings.lineup ?? activeLineup))
  const bar = state?.bar ?? 0
  const meter = activeMeter(state)
  const desiredMeter = next?.settings.meter ?? meter
  const timing = METERS[meter]
  const barTicks = state?.bar_ticks ?? timing.barTicks
  const pulseTicks = state?.pulse_ticks ?? timing.pulseTicks
  const beat = Math.floor(Math.min(state?.tick ?? 0, barTicks - 1) / pulseTicks) + 1
  const timingChange = pending.find((change, index) => {
    const previous = pending[index - 1]?.settings ?? state?.settings
    return change.settings.bpm !== previous?.bpm || (change.settings.meter ?? meter) !== (previous?.meter ?? meter)
  })
  const playing = state?.transport === 'playing'
  const busy = !state || connection !== 'online'
  const songTiming = !!(state?.settings.song || settings?.song)
  const timingHelp = songTiming ? '速度和拍号由歌曲段落控制。退出歌曲后可直接调节。'
    : '以四分音符为速度单位，6/8 的每个大拍含三个八分音符。'
  const songBase = state?.settings.song ? state.settings : settings
  const songSection = state?.settings.song
    ? state.settings.song.sections[state.section?.index ?? 0]
    : songBase?.song?.sections[0]
  const progression = settings?.progression ?? defaultProgression
  const panelChords = songSection?.progression ?? progression
  const sectionStart = state?.section?.start_bar ?? 0
  const activeProgression = state?.settings.progression ?? defaultProgression
  const harmonyUpdate = progression.some((chord, index) => chord !== activeProgression[index])
    ? pending.find(change => change.settings.progression.every((chord, index) => chord === progression[index]))
    : undefined
  const harmonyIndex = state?.section?.bar_in_section ?? bar % progression.length
  const currentHarmony = state?.plans.find(plan => plan.bar === bar && (songSection || plan.improvisation_phrase))?.harmony
  const trackActive = new Set([...active.values()].map(note => note.trackId))
  const status = connection === 'online' ? '引擎已连接' : connection === 'failed' ? '连接失败' : '正在连接'
  const currentTrackSettings = next?.tracks ?? state?.tracks
  const exportUrl = state ? `/sessions/${encodeURIComponent(state.id)}/export.mid` : undefined

  const [dockTab, setDockTab] = useDockTab()
  const remoteCount = Object.keys(state?.remote_agents ?? {}).length
  const dockActivity: Partial<Record<DockTab, boolean>> = {
    conductor: session.conducting || session.improvising || session.composing
      || !!state?.improvisation?.active || !!state?.composition?.planning,
    keyboard: state?.midi?.connection === 'connected',
    score: scoreOwnsAudio,
    sync: !!state?.link?.enabled || remoteCount > 0,
  }
  const notice = error || connection === 'failed' ? (error || '无法连接到本机引擎') : next ? (
    stagedTransition?.transition
      ? `${transitionLabel(stagedTransition.transition)} · ${stagedTransition.pickup_bar != null ? `第 ${stagedTransition.pickup_bar + 1} 小节过门，` : ''}第 ${stagedTransition.bar + 1} 小节起拍`
      : `参数将在第 ${next.bar + 1} 小节生效${timingChange ? ` · 第 ${timingChange.bar + 1} 小节起 ${timingChange.settings.bpm} BPM · ${timingChange.settings.meter ?? meter}` : ''}`
  ) : state?.timing?.waiting_for_downbeat ? '等待 Link 共享小节起点' : ''

  return (
    <div className="app">
      <header className="masthead">
        <div className="masthead-inner">
          <div className="brand">
            <span className="brand-mark"><AudioLines size={20} strokeWidth={2.4} /></span>
            <div>
              <h1>Agent Ensemble</h1>
              <p>{LINEUP_SUBTITLES[activeLineup]}</p>
            </div>
          </div>
          <div className="masthead-actions">
            <LineupSwitch selected={lineup} pending={lineupChange && { bar: lineupChange.bar, lineup }} disabled={busy}
              onChange={selection => { if (selection !== lineup) session.updateSettings({ lineup: selection }) }} />
            <a className={`export ${!exportUrl ? 'disabled' : ''}`} href={exportUrl} download="agent-ensemble.mid" aria-disabled={!exportUrl}
              title="导出当前编排的 MIDI 文件">
              <Download size={16} /><span>导出 MIDI</span>
            </a>
            <span className={`connection ${connection}`} title={status}><i /><span>{status}</span></span>
            <ConductorAgentSwitch />
            <AudioRoute
              state={state}
              ready={session.audioReady}
              audible={session.audible}
              toggle={session.toggleAudio}
              choose={session.chooseAudio}
            />
          </div>
        </div>
        <section className="transport" aria-label="播放控制">
          <div className="transport-buttons">
            <button
              className="play"
              type="button"
              aria-label={playing ? '暂停' : '播放'}
              title={playing ? '暂停' : '播放'}
              onClick={() => session.transport(playing ? 'pause' : 'play')}
              disabled={busy || scoreOwnsAudio}
            >
              {playing ? <Pause size={18} fill="currentColor" /> : <Play size={18} fill="currentColor" />}
            </button>
            <button className="square-button" type="button" aria-label="停止" title="停止" onClick={() => session.transport('stop')} disabled={busy}>
              <Square size={15} fill="currentColor" />
            </button>
            <button className={`square-button loop ${settings?.loop ?? true ? 'enabled' : ''}`} type="button" aria-label="循环" title="循环"
              aria-pressed={settings?.loop ?? true} onClick={() => session.updateSettings({ loop: !settings?.loop })} disabled={busy}>
              <Repeat2 size={18} />
            </button>
          </div>
          <div className="position" aria-label={`第 ${bar + 1} 小节，第 ${beat} ${meter === '6/8' ? '大拍' : '拍'}，${meter} 拍`}>
            <span className="position-number">{decimalBar(bar)}<em>.</em>{String(beat).padStart(2, '0')}</span>
            <span className={`transport-status ${state?.transport ?? ''}`}><i />{playing ? '演奏中' : state?.transport === 'paused' ? '已暂停' : '已停止'}</span>
          </div>
          <div className="setting bpm" title={timingHelp}>
            <label>BPM</label>
            <Editable value={String((songTiming ? state?.bpm : next?.settings.bpm) ?? state?.bpm ?? settings?.bpm ?? 120)} label="BPM" number
              disabled={busy || songTiming} onCommit={value => session.updateSettings({ bpm: Number(value) })} />
          </div>
          <label className="setting meter" title={timingHelp}>
            <small>拍号</small>
            <select aria-label="拍号" value={songTiming ? meter : desiredMeter} disabled={busy || songTiming}
              onChange={event => session.updateSettings({ meter: event.target.value as TimeSignature })}>
              {Object.keys(METERS).map(signature => <option key={signature} value={signature}>{signature}</option>)}
            </select>
          </label>
          <div className="setting density">
            <label>密度</label>
            <Range label="演奏密度" value={settings?.density ?? 0.55} disabled={busy} onCommit={density => session.updateSettings({ density })} />
          </div>
          <div className="transport-info">
            {state?.section && (
              <span className="playing-section" aria-label="当前乐曲段落">
                {SECTION_NAMES[state.section.role]} · {state.section.bar_in_section + 1}/{state.section.end_bar - state.section.start_bar}
              </span>
            )}
            {state?.link?.enabled && (
              <span className="link-chip" title="Ableton Link 已加入">
                <Link2 size={12} aria-hidden="true" />{state.link.peers ? `Link · ${state.link.peers}` : 'Link'}
              </span>
            )}
            {notice && (
              <span className={`announcement ${error || connection === 'failed' ? 'error' : ''}`} role="status" title={notice}>
                <CircleAlert size={14} />
                <span>{notice}</span>
                {connection === 'failed' && <button type="button" onClick={session.retry}>重新连接</button>}
              </span>
            )}
          </div>
        </section>
      </header>

      <main className="layout">
        <div className="stage">
          <section className="panel harmony" aria-label="和弦进行">
            <div className="harmony-label">
              <h2><ListMusic size={15} aria-hidden="true" />{songSection ? `${SECTION_NAMES[songSection.role]}和弦`
                : harmonyUpdate ? '待生效和弦' : currentHarmony ? '主题和弦' : '和弦进行'}</h2>
              <span className={currentHarmony ? 'harmony-playing' : 'heading-meta'}>
                {currentHarmony ? <>{playing ? '正在演奏' : songSection ? '当前' : '即兴和弦'} <strong>{currentHarmony}</strong></>
                  : harmonyUpdate ? `第 ${harmonyUpdate.bar + 1} 小节生效` : `${panelChords.length} 小节 · 可编辑`}
              </span>
            </div>
            <div className="chords">
              {panelChords.map((chord, index) => (
                <div className={`chord ${!harmonyUpdate && harmonyIndex === index ? 'selected' : ''}`} key={index}>
                  <span>{decimalBar(index + sectionStart)}</span>
                  {songSection ? <strong>{chord}</strong> : (
                    <Editable value={chord} label={`第 ${index + 1} 小节和弦`} disabled={busy}
                      onCommit={value => session.updateChord(index, value)} />
                  )}
                </div>
              ))}
            </div>
          </section>

          <PianoRoll state={state} active={active} performed={performed} />

          <section className={`panel mixer${lineupTracks.length > 6 ? ' dense' : ''}`} aria-label="乐器轨道" style={{ '--channels': lineupTracks.length } as CSSProperties}>
            {lineupTracks.map(track => {
              const values = currentTrackSettings?.[track]
              const live = state?.tracks[track]
              const planned = state?.plans.find(plan => plan.bar === bar)
              const fallback = planned?.fallback_tracks.includes(track)
              const remote = state?.remote_agents?.[track]
              const { name, en } = trackLabel(track, lineup)
              const program = values?.program ?? TRACK_INFO[track].program
              const hasSoundOptions = Object.keys(sounds[track]).length > 1
              const status = live?.muted ? '已静音' : fallback ? '规则兜底' : remote ? `远程 · ${remote.name}` : live && planned ? '计划就绪' : '等待计划'
              return (
                <article className="channel" style={accent(track)} key={track} aria-label={name}>
                  <div className="channel-title">
                    <TrackIcon track={track} className="channel-icon" />
                    <div><h3>{name}</h3><p>{en}</p></div>
                    <button type="button" className={`mute ${values?.muted ? 'muted' : ''}`}
                      aria-label={`${name}静音`} aria-pressed={values?.muted ?? false} title={`${name}静音`} disabled={busy}
                      onClick={() => session.updateTrack(track, { muted: !values?.muted })}>
                      {values?.muted ? <VolumeX size={15} /> : <Volume2 size={15} />}
                    </button>
                  </div>
                  <div className="channel-volume">
                    <Range label={`${name}音量`} value={values?.volume ?? 0.8} disabled={busy} onCommit={volume => session.updateTrack(track, { volume })} />
                  </div>
                  <div className="program">
                    <select aria-label={`${name}音色`} value={program} disabled={busy || !hasSoundOptions}
                      onChange={event => session.updateTrack(track, { program: Number(event.target.value) })}>
                      {Object.entries(sounds[track]).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                      {sounds[track][program] === undefined && <option value={program}>GM 音色 {program + 1}</option>}
                    </select>
                    {hasSoundOptions && <ChevronsUpDown size={12} />}
                  </div>
                  <div className="channel-footer">
                    <span className={fallback ? 'fallback' : remote ? 'remote' : ''} title={status}><i />{status}</span>
                    <div className={`signal ${trackActive.has(track) ? 'live' : ''}`} aria-hidden="true">{Array.from({ length: 6 }, (_, index) => <i key={index} />)}</div>
                  </div>
                </article>
              )
            })}
          </section>
        </div>

        <aside className="dock" aria-label="工具面板">
          <div className="dock-tabs" role="tablist" aria-label="工具">
            {DOCK_TABS.map(({ id, label, icon: Icon }) => (
              <button key={id} type="button" role="tab" id={`dock-tab-${id}`} aria-controls={`dock-panel-${id}`}
                aria-selected={dockTab === id} onClick={() => setDockTab(id)}>
                <Icon size={15} aria-hidden="true" />{label}
                {dockActivity[id] && <i className="dock-dot" aria-label="有活动" />}
              </button>
            ))}
          </div>
          <div className="dock-body">
            <div role="tabpanel" id="dock-panel-conductor" aria-labelledby="dock-tab-conductor" hidden={dockTab !== 'conductor'}>
              {state && 'conductor' in state && (
                <ConductorPanel
                  key={`conductor-${state.id}`}
                  state={state}
                  disabled={busy}
                  conducting={session.conducting}
                  improvising={session.improvising}
                  composing={session.composing}
                  error={session.conductError}
                  onConduct={session.conduct}
                  onPreset={session.setPreset}
                  onImprovise={session.improvise}
                  onCompose={session.compose}
                  onLeaveSong={session.leaveSong}
                  onAuto={session.setAutoImprovise}
                  onJam={session.updateJam}
                />
              )}
            </div>
            <div role="tabpanel" id="dock-panel-keyboard" aria-labelledby="dock-tab-keyboard" hidden={dockTab !== 'keyboard'}>
              {state?.midi && (
                <MidiPanel
                  key={`midi-${state.id}`}
                  midi={state.midi}
                  state={state}
                  disabled={busy}
                  audioEnabled={state.audio_output === 'native' || state.audio_output === 'supercollider'
                    || (session.audioReady && session.audible)}
                  enableAudio={session.toggleAudio}
                  onChange={session.updateMidi}
                />
              )}
            </div>
            <div role="tabpanel" id="dock-panel-score" aria-labelledby="dock-tab-score" hidden={dockTab !== 'score'}>
              <MidiScorePlayer onPrepare={session.pauseForScore} onPlayingChange={setScoreOwnsAudio} />
            </div>
            <div role="tabpanel" id="dock-panel-negotiation" aria-labelledby="dock-tab-negotiation" hidden={dockTab !== 'negotiation'}>
              {state && 'negotiation' in state && (
                <NegotiationPanel
                  key={`negotiation-${state.id}`}
                  state={state}
                  disabled={busy}
                  alwaysOpen
                  onModeChange={collaboration => session.updateSettings({ collaboration })}
                />
              )}
            </div>
            <div role="tabpanel" id="dock-panel-sync" aria-labelledby="dock-tab-sync" hidden={dockTab !== 'sync'}>
              {state && <SyncPanel state={state} disabled={busy} onLink={session.updateLink} />}
            </div>
          </div>
        </aside>
      </main>
    </div>
  )
}

createRoot(document.getElementById('root')!).render(<App />)
