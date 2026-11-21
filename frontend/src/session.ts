import { useCallback, useEffect, useRef, useState } from 'react'
import { TRACK_IDS } from './model'
import type { AudioOutput, BandEvent, Frame, LinkPatch, MidiPatch, Settings, SoundNote, State, Style, TrackId, TrackSettings } from './model'
import { StudioSynth } from './synth'

type Connection = 'starting' | 'online' | 'retrying' | 'failed'
type Command = (state: State) => { path: string; values?: object; method?: 'PATCH' | 'POST' | 'DELETE' }

class RequestError extends Error {
  constructor(message: string, readonly status: number) {
    super(message)
  }
}

function usesBrowserAudio(state: State | null) {
  return !state?.audio_output || state.audio_output === 'browser' || state.audio_fallback === 'browser'
}

function playableAttack(event: BandEvent, state: State) {
  if (event.type === 'midi_on') return state.midi?.connection === 'connected'
  return event.type === 'note_on' && state.transport === 'playing'
    && !!event.track_id && state.tracks[event.track_id]?.muted === false
}

function restoreVoices(audio: StudioSynth, state: State, notes = state.audio_active ?? []) {
  audio.resume(notes.filter(event => playableAttack(event, state)))
}

async function request(path: string, method = 'GET', values?: object, signal?: AbortSignal): Promise<State> {
  const response = await fetch(path, {
    method,
    signal,
    headers: values ? { 'Content-Type': 'application/json' } : undefined,
    body: values ? JSON.stringify(values) : undefined,
  })
  if (!response.ok) {
    const result = await response.json().catch(() => ({}))
    const detail = result.detail
    const message = typeof detail === 'string' ? detail : detail?.[0]?.msg
    throw new RequestError(message || `请求失败（${response.status}）`, response.status)
  }
  return response.json()
}

export function useSession() {
  const [state, setState] = useState<State | null>(null)
  const [connection, setConnection] = useState<Connection>('starting')
  const [error, setError] = useState('')
  const [audible, setAudible] = useState(true)
  const [audioReady, setAudioReady] = useState(false)
  const [attempt, setAttempt] = useState(0)
  const [conducting, setConducting] = useState(false)
  const [improvising, setImprovising] = useState(false)
  const [composing, setComposing] = useState(false)
  const [conductError, setConductError] = useState('')
  const stateRef = useRef<State | null>(null)
  const synth = useRef<StudioSynth | null>(null)
  const operation = useRef<Promise<void>>(Promise.resolve())
  const conductRequest = useRef<AbortController | null>(null)
  const active = useRef(new Map<string, SoundNote>())
  const performed = useRef(new Map<string, number>())

  const apply = useCallback((next: State) => {
    const previous = stateRef.current
    if (previous?.id === next.id && previous.sequence > next.sequence) return
    if (next.transport !== 'playing') {
      synth.current?.silenceBand()
      active.current.clear()
      performed.current.clear()
    }
    if (next.midi) {
      synth.current?.setPlayerVolume(next.midi.volume)
      if (next.midi.connection !== 'connected') synth.current?.silence('player')
    }
    for (const track of TRACK_IDS) {
      if (next.tracks[track]?.muted !== false && previous?.tracks[track]?.muted === false) {
        synth.current?.silenceBand(track)
        for (const [id, note] of active.current) {
          if (note.trackId === track) active.current.delete(id)
        }
      }
    }
    stateRef.current = next
    setState(next)
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    let socket: WebSocket | undefined
    let reconnect: number | undefined
    const startModern = new URLSearchParams(location.search).get('lineup') === 'modern'
    const sessionKey = startModern ? 'agent-ensemble-modern-id' : 'agent-ensemble-id'
    const firstId = sessionStorage.getItem(sessionKey)
    const audio = new StudioSynth()
    synth.current = audio
    setAudioReady(false)

    function connect(id: string) {
      if (controller.signal.aborted) return
      let cursor = -1
      let route: AudioOutput | undefined
      const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
      socket = new WebSocket(`${protocol}//${location.host}/sessions/${encodeURIComponent(id)}/events`)
      socket.onmessage = (message) => {
        const frame = JSON.parse(message.data) as Frame
        const now = performance.now()
        setConnection('online')
        const current = stateRef.current
        const live = current?.id === frame.state.id && current.sequence > frame.state.sequence ? current : frame.state
        if (live.midi) audio.setPlayerVolume(live.midi.volume)
        if (cursor === -1 || frame.reset) {
          audio.silence()
          active.current.clear()
          performed.current.clear()
          route = frame.state.audio_output ?? 'browser'
          if (route === 'browser') restoreVoices(audio, frame.state)
        }
        for (const event of frame.reset ? [] : frame.events) {
          if (event.sequence <= cursor) continue
          cursor = event.sequence
          if (event.type === 'audio_route') {
            route = event.audio_output ?? frame.state.audio_output ?? 'browser'
            audio.silence()
            if (route === 'browser') restoreVoices(audio, live, event.resume ?? [])
            continue
          }
          if (event.type === 'silence') {
            for (const [id, note] of active.current) {
              if (!event.track_id || note.trackId === event.track_id) active.current.delete(id)
            }
          }
          if (event.type === 'note_on') {
            if (!playableAttack(event, live)) continue
            if (event.event_id && event.track_id) {
              active.current.set(event.event_id, { trackId: event.track_id, expiry: now + 4000 })
              performed.current.set(event.event_id, event.bar ?? frame.state.bar)
            }
          }
          if (event.type === 'note_off' && event.event_id) {
            const note = active.current.get(event.event_id)
            if (note) note.expiry = Math.min(note.expiry, now + 100)
          }
          if (event.type === 'midi_on' && !playableAttack(event, live)) continue
          if ((event.audio_output ?? route ?? 'browser') === 'browser') audio.event(event)
        }
        cursor = Math.max(cursor, frame.state.sequence)
        for (const [id, note] of active.current) {
          if (note.expiry < now) active.current.delete(id)
        }
        for (const [id, bar] of performed.current) {
          if (bar < frame.state.bar) performed.current.delete(id)
        }
        apply(frame.state)
      }

      socket.onclose = () => {
        if (controller.signal.aborted) return
        audio.silence()
        active.current.clear()
        performed.current.clear()
        setConnection('retrying')
        reconnect = window.setTimeout(() => void initialize(id), 1100)
      }
    }

    async function initialize(id: string | null) {
      try {
        let snapshot: State | undefined
        if (id) {
          try {
            snapshot = await request(`/sessions/${encodeURIComponent(id)}/state`, 'GET', undefined, controller.signal)
          } catch (failure) {
            if (!(failure instanceof RequestError) || failure.status !== 404) throw failure
          }
        }
        snapshot ??= await request('/sessions', 'POST', startModern ? { lineup: 'modern' } : undefined, controller.signal)
        if (controller.signal.aborted) return
        apply(snapshot)
        setError('')
        sessionStorage.setItem(sessionKey, snapshot.id)
        connect(snapshot.id)
      } catch (failure) {
        if (controller.signal.aborted) return
        setConnection('failed')
        setError(failure instanceof Error ? failure.message : '无法连接乐队引擎')
        reconnect = window.setTimeout(() => void initialize(id), 2500)
      }
    }

    void initialize(firstId)
    return () => {
      controller.abort()
      socket?.close()
      window.clearTimeout(reconnect)
      active.current.clear()
      performed.current.clear()
      audio.close()
    }
  }, [apply, attempt])

  useEffect(() => {
    setConducting(false)
    setImprovising(false)
    setComposing(false)
    setConductError('')
    return () => {
      conductRequest.current?.abort()
      conductRequest.current = null
    }
  }, [state?.id])

  function send(command: Command) {
    setError('')
    const queued = operation.current.then(async () => {
      const current = stateRef.current
      if (!current) return null
      const { path, values, method = values ? 'PATCH' : 'POST' } = command(current)
      try {
        const next = await request(`/sessions/${encodeURIComponent(current.id)}/${path}`, method, values)
        if (stateRef.current?.id === current.id) apply(next)
        return next
      } catch (failure) {
        if (stateRef.current?.id === current.id) {
          setError(failure instanceof Error ? failure.message : '控制请求失败')
        }
        return null
      }
    })
    operation.current = queued.then(() => {})
    return queued
  }

  function updateSettings(values: Partial<Settings>) {
    send(() => ({ path: 'settings', values }))
  }

  function updateChord(index: number, chord: string) {
    send(current => {
      const next = current.pending_changes?.at(-1) ?? current.pending
      const progression = [...(next?.settings ?? current.settings).progression]
      progression[index] = chord
      return { path: 'settings', values: { progression } }
    })
  }

  function updateTrack(track: TrackId, values: Partial<TrackSettings>) {
    send(() => ({ path: `tracks/${track}`, values }))
  }

  function updateLink(values: LinkPatch) {
    send(() => ({ path: 'link', values }))
  }

  function updateMidi(values: MidiPatch) {
    if (values.port && audible && usesBrowserAudio(stateRef.current)) unlockAudio()
    send(() => ({ path: 'midi', values }))
  }

  async function requestConducting(path: 'conduct' | 'improvisation' | 'composition', values: { instruction?: string }) {
    const current = stateRef.current
    if (!current || conductRequest.current) return

    const controller = new AbortController()
    conductRequest.current = controller
    setConductError('')
    const setPending = { conduct: setConducting, improvisation: setImprovising, composition: setComposing }[path]
    setPending(true)
    try {
      const next = await request(
        `/sessions/${encodeURIComponent(current.id)}/${path}`,
        'POST', values, controller.signal,
      )
      if (stateRef.current?.id === current.id) apply(next)
    } catch (failure) {
      if (controller.signal.aborted || stateRef.current?.id !== current.id) return
      if (failure instanceof RequestError && failure.status === 409) {
        setConductError('指挥正在处理其他指令，或当前编排已更新。请重试。')
      } else if (failure instanceof RequestError && failure.status === 429) {
        setConductError('指令提交过于频繁，请稍后重试。')
      } else {
        setConductError(failure instanceof RequestError ? failure.message : '提交指令失败，请检查引擎连接后重试。')
      }
    } finally {
      if (conductRequest.current === controller) {
        conductRequest.current = null
        setPending(false)
      }
    }
  }

  function conduct(instruction: string) {
    return requestConducting('conduct', { instruction })
  }

  function improvise(instruction: string) {
    return requestConducting('improvisation', instruction ? { instruction } : {})
  }

  function compose(instruction: string) {
    return requestConducting('composition', { instruction })
  }

  function leaveSong() {
    setConductError('')
    send(() => ({ path: 'composition', method: 'DELETE' }))
  }

  function setPreset(style: Style) {
    setConductError('')
    send(() => ({ path: 'preset', method: 'POST', values: { style } }))
  }

  function setAutoImprovise(auto: boolean) {
    send(() => ({ path: 'improvisation', values: { auto } }))
  }

  function unlockAudio() {
    const audio = synth.current
    void audio?.unlock()
      .then(() => {
        if (synth.current !== audio) return
        setAudioReady(true)
        const latest = stateRef.current
        if (latest && (!latest.audio_output || latest.audio_output === 'browser')) {
          if (latest.midi) audio.setPlayerVolume(latest.midi.volume)
          restoreVoices(audio, latest)
        }
      })
      .catch(() => setError('浏览器音频无法启动'))
  }

  function transport(path: 'play' | 'pause' | 'stop') {
    if (path === 'play' && audible && usesBrowserAudio(stateRef.current)) unlockAudio()
    if (path !== 'play') synth.current?.silenceBand()
    return send(() => ({ path }))
  }

  async function pauseForScore() {
    // Resolve any transport request already queued before the score took ownership.
    await operation.current
    if (stateRef.current?.transport === 'playing') {
      const result = await transport('pause')
      if (result?.transport !== 'paused') throw new Error('请先暂停乐队，再播放 MIDI 曲目。')
    }
  }

  function toggleAudio() {
    const enabled = !audioReady || !audible
    setAudible(enabled)
    synth.current?.toggle(enabled)
    if (enabled) unlockAudio()
  }

  function retry() {
    setError('')
    setConnection('starting')
    setAttempt(value => value + 1)
  }

  return {
    state, connection, error, audible, audioReady, toggleAudio, transport, pauseForScore,
    updateSettings, updateChord, updateTrack, updateMidi, updateLink, retry, conduct, conducting, conductError,
    improvise, improvising, compose, composing, leaveSong, setPreset, setAutoImprovise,
    active: active.current, performed: performed.current,
  }
}
