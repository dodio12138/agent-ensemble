import { useEffect, useId, useState } from 'react'
import { Piano, Volume2 } from 'lucide-react'
import { CORE_TRACK_IDS, TRACK_INFO, pitchName, type MidiPatch, type MidiState, type PlayerResponse, type State } from './model'
import { Range } from './Range'

const monitoringSounds: Record<number, string> = {
  0: '三角钢琴', 1: '明亮钢琴', 4: '电钢琴', 11: '颤音琴', 19: '管风琴',
}
const blackKeys = new Set([1, 3, 6, 8, 10])
const keyboardSpan = 24

function MidiKeys({ pressed, sounding }: { pressed: number[]; sounding: number[] }) {
  const [start, setStart] = useState(48)
  const sustain = new Set(sounding)
  const held = new Set(pressed)

  let lowest = 127
  let highest = 0
  for (const pitch of pressed.length ? pressed : sounding) {
    lowest = Math.min(lowest, pitch)
    highest = Math.max(highest, pitch)
  }
  const hasNotes = pressed.length + sounding.length > 0
  useEffect(() => {
    if (hasNotes && (lowest < start || highest >= start + keyboardSpan)) {
      const lowestOctave = Math.floor(lowest / 12) * 12
      const highestWindow = Math.floor(highest / 12) * 12 - 12
      setStart(Math.min(108, Math.max(lowestOctave, highestWindow)))
    }
  }, [hasNotes, lowest, highest, start])

  const pitches = Array.from({ length: Math.min(keyboardSpan, 128 - start) }, (_, index) => start + index)
  const whites = pitches.filter(pitch => !blackKeys.has(pitch % 12))
  const keyClass = (pitch: number) => held.has(pitch) ? 'held' : sustain.has(pitch) ? 'sustained' : ''
  const active = Array.from(new Set([...pressed, ...sounding])).sort((a, b) => a - b)
  const notes = active.slice(0, 8).map(pitchName)
  if (active.length > notes.length) notes.push(`+ ${active.length - notes.length}`)

  return (
    <div className="midi-preview">
      <div className="midi-keys" aria-hidden="true">
        {whites.map(pitch => (
          <div className={`midi-key-white ${keyClass(pitch)}`} key={pitch} title={pitchName(pitch)}>
            {pitch % 12 === 0 && <span>{pitchName(pitch)}</span>}
          </div>
        ))}
        {pitches.filter(pitch => blackKeys.has(pitch % 12)).map(pitch => {
          const left = whites.filter(white => white < pitch).length / whites.length * 100
          return <div className={`midi-key-black ${keyClass(pitch)}`} key={pitch} title={pitchName(pitch)}
            style={{ left: `${left}%`, width: `${65 / whites.length}%` }} />
        })}
      </div>
      <p className="midi-note-names" aria-label="正在发声的键盘音符">{notes.join(' · ') || '等待弹奏'}</p>
    </div>
  )
}

function PhraseResponse({ title, response }: { title: string; response: PlayerResponse }) {
  const { phrase } = response
  const register = { low: '低音区', middle: '中音区', high: '高音区' }[phrase.register]
  return (
    <div className="midi-response">
      <p><strong>{title}</strong><span>{register} · {phrase.activity === 'busy' ? '密集' : '稀疏'} · {phrase.velocity === 'strong' ? '强奏' : '轻奏'}</span></p>
      <dl>
        {CORE_TRACK_IDS.map(track => (
          <div key={track}>
            <dt style={{ color: TRACK_INFO[track].color }}>{TRACK_INFO[track].name}</dt>
            <dd>{response[track]}</dd>
          </div>
        ))}
      </dl>
    </div>
  )
}

export function MidiPanel({ midi, state, disabled, audioEnabled, enableAudio, onChange }: {
  midi: MidiState
  state: State
  disabled: boolean
  audioEnabled: boolean
  enableAudio: () => void
  onChange: (patch: MidiPatch) => void
}) {
  const portId = useId()
  const soundId = useId()
  const connected = midi.connection === 'connected'
  const selectedPortPresent = midi.ports.some(port => port.id === midi.selected)
  const status = {
    connected: '已连接',
    disabled: midi.ports.length ? '选择输入设备' : '未检测到 MIDI 输入',
    waiting: '等待键盘连接',
    unavailable: 'MIDI 输入暂不可用',
  }[midi.connection]
  const current = state.plans.find(plan => plan.bar === state.bar && plan.player_response)
  const upcoming = state.plans.filter(plan => plan.bar > state.bar && plan.player_response)
    .sort((a, b) => a.bar - b.bar)[0]

  return (
    <section className="panel midi-panel" aria-label="MIDI 键盘">
      <header className="panel-heading">
        <h2><Piano size={17} aria-hidden="true" />MIDI 键盘</h2>
        <span className={`midi-status ${midi.connection}`} role="status" title={midi.error ?? undefined}>
          <i aria-hidden="true" />{status}
        </span>
      </header>
      <div className="midi-body">
        <div className="midi-controls">
          <label className="midi-field" htmlFor={portId}>
            <span>输入设备</span>
            <select id={portId} aria-label="MIDI 输入设备" value={midi.selected ?? ''} disabled={disabled}
              onChange={event => onChange({ port: event.target.value || null })}>
              <option value="">未选择</option>
              {midi.ports.map(port => <option value={port.id} key={port.id}>{port.name}</option>)}
              {midi.selected && !selectedPortPresent && <option value={midi.selected}>已选设备（等待重连）</option>}
            </select>
          </label>
          <label className="midi-field" htmlFor={soundId}>
            <span>监听音色</span>
            <select id={soundId} aria-label="键盘监听音色" value={midi.program} disabled={disabled}
              onChange={event => onChange({ program: Number(event.target.value) })}>
              {Object.entries(monitoringSounds).map(([program, label]) => <option value={program} key={program}>{label}</option>)}
              {monitoringSounds[midi.program] === undefined && <option value={midi.program}>GM 音色 {midi.program + 1}</option>}
            </select>
          </label>
          <div className="midi-field">
            <span>监听音量</span>
            <Range label="键盘监听音量" value={midi.volume} disabled={disabled} onCommit={volume => onChange({ volume })} />
          </div>
          <div className="midi-actions">
            <label className="midi-follow">
              <input type="checkbox" checked={midi.adaptive} disabled={disabled} onChange={event => onChange({ adaptive: event.target.checked })} />
              乐队跟随弹奏
            </label>
            {connected && !audioEnabled && (
              <button type="button" className="midi-monitor" onClick={enableAudio}><Volume2 size={15} aria-hidden="true" />启用监听</button>
            )}
          </div>
        </div>
        <MidiKeys pressed={midi.pressed} sounding={midi.sounding} />
      </div>
      {(current?.player_response || upcoming?.player_response) && (
        <div className="midi-responses" aria-label="伴奏对键盘的响应">
          {current?.player_response && <PhraseResponse title={`第 ${current.bar + 1} 小节 · 当前响应`} response={current.player_response} />}
          {upcoming?.player_response && <PhraseResponse title={`第 ${upcoming.bar + 1} 小节 · 待演奏`} response={upcoming.player_response} />}
        </div>
      )}
    </section>
  )
}
