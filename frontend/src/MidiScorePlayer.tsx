import { useEffect, useRef, useState, type ChangeEvent } from 'react'
import { FileMusic, Pause, Play, Square } from 'lucide-react'
import type { BasicMIDI } from 'spessasynth_core'
import type { Sequencer, WorkletSynthesizer } from 'spessasynth_lib'
import processorUrl from 'spessasynth_lib/dist/spessasynth_processor.min.js?url'
import './MidiScorePlayer.css'

type TimingMark<T> = { seconds: number; value: T }
type Score = {
  name: string
  midi: BasicMIDI
  duration: number
  tracks: number
  tempos: TimingMark<number>[]
  meters: TimingMark<string>[]
}

type Engine = {
  context: AudioContext
  synthesizer: WorkletSynthesizer
  sequencer: Sequencer
  score: Score | null
}
type Status = 'idle' | 'reading' | 'loading' | 'playing' | 'paused' | 'ended'

const maximumSize = 16 * 1024 * 1024

function at<T>(marks: TimingMark<T>[], seconds: number, initial: T): T {
  return marks.findLast(mark => mark.seconds <= seconds)?.value ?? initial
}

function clock(seconds: number) {
  const total = Math.max(0, Math.floor(seconds))
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`
}

async function readScore(file: File): Promise<Score> {
  if (file.size > maximumSize) throw new Error('MIDI 文件不能超过 16 MB。')
  const bytes = await file.arrayBuffer()
  const header = new DataView(bytes)
  if (bytes.byteLength < 14 || header.getUint32(0) !== 0x4d546864 || header.getUint32(4) < 6) {
    throw new Error('请选择有效的标准 MIDI 文件（.mid 或 .midi）。')
  }
  const format = header.getUint16(8)
  const division = header.getUint16(12)
  if (format > 1 || !division || division & 0x8000) {
    throw new Error('支持按节拍计时的 0 型和 1 型 MIDI 文件。')
  }
  const { BasicMIDI, MIDIMessage, MIDIMessageTypes } = await import('spessasynth_core')
  const midi = BasicMIDI.fromArrayBuffer(bytes, file.name)
  const first = midi.timeline[0]
  if (!first || !midi.timeline.some(({ tr, ev }) => {
    const event = midi.tracks[tr].events[ev]
    return (event.statusByte & 0xf0) === 0x90 && event.data[1] > 0
  })) {
    throw new Error('这份 MIDI 没有可播放的音符。')
  }
  if (midi.tracks[first.tr].events[first.ev].ticks > 0) {
    // Anchor the transport at tick zero even when every original event starts later.
    midi.tracks[0].addEvents(0, new MIDIMessage(0, MIDIMessageTypes.setTempo, new Uint8Array([0x07, 0xa1, 0x20])))
    midi.flush(false)
  }
  const last = midi.timeline.at(-1)!
  const lastTick = midi.tracks[last.tr].events[last.ev].ticks
  const duration = midi.midiTicksToSeconds(lastTick)
  if (!Number.isFinite(duration) || duration <= 0) throw new Error('这份 MIDI 的时间信息无效。')
  // The sequencer normally ends on the first voice event at the final voice tick.
  // Let it consume all events at that tick and the written end-of-track silence.
  midi.lastVoiceEventTick = lastTick + 1
  midi.duration = duration
  const tempos = midi.tempoChanges.toReversed().map(change => ({
    seconds: midi.midiTicksToSeconds(change.ticks),
    value: Math.round(change.tempo * 10) / 10,
  }))
  const meters = midi.timeline.flatMap(({ tr, ev }) => {
    const event = midi.tracks[tr].events[ev]
    if (event.statusByte !== MIDIMessageTypes.timeSignature || event.data.length < 2 || event.data[1] > 8) return []
    return [{ seconds: midi.midiTicksToSeconds(event.ticks), value: `${event.data[0]}/${2 ** event.data[1]}` }]
  })
  return { name: file.name, midi, duration, tracks: midi.tracks.length, tempos, meters }
}

async function initialize(context: AudioContext, signal: AbortSignal): Promise<Engine> {
  const [{ Sequencer, WorkletSynthesizer }, response] = await Promise.all([
    import('spessasynth_lib'),
    fetch('/score-soundfont', { signal }),
    context.audioWorklet.addModule(processorUrl),
  ])
  if (!response.ok) throw new Error('本地 GM 音色库不可用。')
  const bank = await response.arrayBuffer()
  signal.throwIfAborted()
  const synthesizer = new WorkletSynthesizer(context)
  try {
    synthesizer.connect(context.destination)
    await synthesizer.soundBankManager.addSoundBank(bank, 'general-user-gs')
    await synthesizer.isReady
    signal.throwIfAborted()
    const sequencer = new Sequencer(synthesizer, { skipToFirstNoteOn: false })
    return { context, synthesizer, sequencer, score: null }
  } catch (error) {
    synthesizer.destroy()
    throw error
  }
}

function loadScore(engine: Engine, score: Score, signal: AbortSignal) {
  const { sequencer } = engine
  return new Promise<void>((resolve, reject) => {
    const settle = (error?: unknown) => {
      window.clearTimeout(timeout)
      signal.removeEventListener('abort', abort)
      sequencer.eventHandler.removeEvent('songChange', 'score-load')
      sequencer.eventHandler.removeEvent('midiError', 'score-load')
      if (error) reject(error)
      else resolve()
    }
    const abort = () => settle(signal.reason)
    const timeout = window.setTimeout(() => settle(new Error('读取 MIDI 声部超时，请重试。')), 15000)
    sequencer.eventHandler.addEvent('songChange', 'score-load', () => settle())
    sequencer.eventHandler.addEvent('midiError', 'score-load', () => settle(new Error('无法读取此 MIDI 曲目。')))
    signal.addEventListener('abort', abort, { once: true })
    sequencer.loadNewSongList([score.midi])
  })
}

function quiet(engine: Engine | null, rewind = false) {
  if (!engine?.score) return
  const { sequencer, synthesizer } = engine
  sequencer.pause()
  if (rewind) sequencer.currentTime = 0
  synthesizer.stopAll(true)
}

export function MidiScorePlayer({ onPrepare, onPlayingChange }: {
  onPrepare: () => Promise<void>
  onPlayingChange: (playing: boolean) => void
}) {
  const [score, setScore] = useState<Score | null>(null)
  const [status, setStatus] = useState<Status>('idle')
  const [seconds, setSeconds] = useState(0)
  const [error, setError] = useState('')
  const currentEngine = useRef<Engine | null>(null)
  const pending = useRef<AbortController | null>(null)
  const reading = useRef(0)
  const context = useRef<AudioContext | null>(null)

  useEffect(() => () => {
    reading.current++
    pending.current?.abort()
    currentEngine.current?.synthesizer.destroy()
    if (context.current?.state !== 'closed') void context.current?.close()
  }, [])

  useEffect(() => {
    if (status !== 'playing') return
    const engine = currentEngine.current
    if (!engine || !score) return
    const played = window.setInterval(() => {
      const now = engine.sequencer.currentTime
      setSeconds(engine.sequencer.isFinished ? score.duration : Math.min(score.duration, now))
      if (!engine.sequencer.isFinished) return
      setStatus('ended')
      onPlayingChange(false)
    }, 100)
    return () => window.clearInterval(played)
  }, [score, status, onPlayingChange])

  async function choose(event: ChangeEvent<HTMLInputElement>) {
    const file = event.currentTarget.files?.[0]
    event.currentTarget.value = ''
    if (!file) return
    const selection = ++reading.current
    pending.current?.abort()
    quiet(currentEngine.current)
    onPlayingChange(false)
    setStatus('reading')
    setSeconds(0)
    setError('')
    setScore(null)
    try {
      const loaded = await readScore(file)
      if (selection !== reading.current) return
      setScore(loaded)
    } catch (failure) {
      if (selection !== reading.current) return
      setError(failure instanceof Error ? failure.message : '无法读取此 MIDI 曲目。')
    } finally {
      if (selection === reading.current) setStatus('idle')
    }
  }

  async function play() {
    if (!score || pending.current) return
    if (status === 'playing') {
      quiet(currentEngine.current)
      setStatus('paused')
      onPlayingChange(false)
      return
    }
    const task = new AbortController()
    pending.current = task
    onPlayingChange(true)
    setError('')
    setStatus('loading')
    let player = currentEngine.current
    try {
      if (!context.current || context.current.state === 'closed') context.current = new AudioContext()
      const ctx = context.current
      const resumed = ctx.resume()
      // Start resume synchronously from the click even when the SoundFont has not loaded.
      if (!player) [player] = await Promise.all([initialize(ctx, task.signal), resumed])
      else await resumed
      task.signal.throwIfAborted()
      currentEngine.current = player
      if (player.score !== score) {
        await loadScore(player, score, task.signal)
        player.score = score
      }
      task.signal.throwIfAborted()
      await onPrepare()
      task.signal.throwIfAborted()
      player.sequencer.loopCount = 0
      if (status !== 'paused') {
        player.sequencer.currentTime = 0
        setSeconds(0)
      }
      player.sequencer.play()
      setStatus('playing')
    } catch (failure) {
      if (task.signal.aborted) {
        if (player !== currentEngine.current) player?.synthesizer.destroy()
        return
      }
      quiet(player)
      setError(failure instanceof Error ? failure.message : '原谱播放器无法启动。')
      setStatus('idle')
      onPlayingChange(false)
    } finally {
      if (pending.current === task) pending.current = null
    }
  }

  function stop() {
    pending.current?.abort()
    quiet(currentEngine.current, true)
    setSeconds(0)
    setStatus('idle')
    onPlayingChange(false)
  }

  const duration = score?.duration ?? 0
  const detail = score
    ? `${score.tracks} 条轨道 · ${at(score.tempos, seconds, 120)} BPM · ${at(score.meters, seconds, '4/4')}`
    : '选择本地 MIDI，按原谱的音符、速度和音色指令演奏。'
  return (
    <section className="panel midi-score-panel" aria-labelledby="midi-score-title">
      <div className="panel-heading">
        <div className="midi-score-title"><FileMusic size={17} /><h2 id="midi-score-title">MIDI 原谱</h2></div>
        <label className="midi-score-picker">
          <input type="file" accept=".mid,.midi,audio/midi,audio/x-midi" aria-label="打开 MIDI 原谱" onChange={event => void choose(event)} />
          打开 MIDI
        </label>
      </div>
      <div className="midi-score-body">
        <div className="midi-score-file">
          {score && <strong title={score.name}>{score.name}</strong>}
          <span>{status === 'reading' ? '读取曲目…' : detail}</span>
        </div>
        {score && (
          <div className="midi-score-progress">
            <progress max={duration} value={Math.max(0, seconds)} aria-label="MIDI 曲目播放进度" />
            <span>{clock(seconds)} / {clock(duration)}</span>
          </div>
        )}
        <div className="midi-score-controls">
          <button type="button" className="square-button" aria-label={status === 'playing' ? '暂停原谱' : '播放原谱'}
            title={status === 'playing' ? '暂停原谱' : '播放原谱'}
            disabled={!score || status === 'loading'} onClick={() => void play()}>
            {status === 'playing' ? <Pause size={16} fill="currentColor" /> : <Play size={16} fill="currentColor" />}
          </button>
          <button type="button" className="square-button" aria-label="停止原谱" title="停止原谱"
            disabled={status === 'idle' || status === 'reading'} onClick={stop}><Square size={13} fill="currentColor" /></button>
        </div>
        {status === 'loading' && <small className="midi-score-notice" role="status">正在加载本地音色库与曲目…</small>}
        {error && <small className="midi-score-notice midi-score-error" role="alert">{error}</small>}
      </div>
    </section>
  )
}
