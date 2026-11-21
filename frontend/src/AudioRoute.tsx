import { Check, ChevronDown, Volume2, VolumeX } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import type { AudioOutput, State } from './model'

const fallbackLabels: Record<string, string> = {
  starting: 'SuperCollider 连接中',
  unavailable: 'SuperCollider 暂不可用',
  disconnected: 'SuperCollider 已断开',
}

function routeLabel(route: AudioOutput, state: State | null) {
  if (route === 'supercollider') return 'SuperCollider'
  if (route === 'native') return state?.audio_device === 'logic' ? 'Logic Pro' : 'FluidSynth 设备音频'
  return '浏览器音频'
}

function routeDetail(route: AudioOutput, state: State | null) {
  if (route === 'supercollider') return '本机 SuperCollider 合成器'
  if (route === 'native') return state?.audio_device === 'logic' ? '经虚拟 MIDI 口，由 Logic 的音源发声' : '本机 SoundFont 采样'
  return '在这个网页里直接发声'
}

export function AudioRoute({ state, ready, audible, toggle, choose }: {
  state: State | null
  ready: boolean
  audible: boolean
  toggle: () => void
  choose: (output: AudioOutput) => void
}) {
  const routes = state?.audio_routes ?? []
  if (routes.length > 1) return <AudioRouteMenu state={state} routes={routes} ready={ready} audible={audible} toggle={toggle} choose={choose} />
  return <AudioRouteStatus state={state} ready={ready} audible={audible} toggle={toggle} />
}

function AudioRouteMenu({ state, routes, ready, audible, toggle, choose }: {
  state: State | null
  routes: AudioOutput[]
  ready: boolean
  audible: boolean
  toggle: () => void
  choose: (output: AudioOutput) => void
}) {
  const [open, setOpen] = useState(false)
  const root = useRef<HTMLDivElement>(null)
  const output = state?.audio_output ?? 'browser'
  const pending = state?.audio_preferred === 'supercollider' && output !== 'supercollider'
    ? fallbackLabels[state.audio_reason ?? 'unavailable'] ?? fallbackLabels.unavailable
    : undefined
  const muted = output === 'browser' && !(ready && audible)

  useEffect(() => {
    if (!open) return
    const close = (event: PointerEvent | KeyboardEvent) => {
      if (event instanceof KeyboardEvent ? event.key === 'Escape' : !root.current?.contains(event.target as Node)) setOpen(false)
    }
    document.addEventListener('pointerdown', close)
    document.addEventListener('keydown', close)
    return () => { document.removeEventListener('pointerdown', close); document.removeEventListener('keydown', close) }
  }, [open])

  return (
    <div className="agent-switch" ref={root}>
      <button type="button" className="audio-toggle" aria-expanded={open} aria-haspopup="dialog" title="切换声音输出"
        onClick={() => setOpen(value => !value)}>
        {muted ? <VolumeX size={17} aria-hidden="true" /> : <Volume2 size={17} aria-hidden="true" />}
        <span className="audio-label">
          <span>{routeLabel(output, state)}</span>
          {(pending || muted) && <small>{pending ?? (ready ? '已静音' : '点此启用')}</small>}
        </span>
        <ChevronDown size={13} aria-hidden="true" />
      </button>
      {open && (
        <div className="agent-popover audio-popover" role="dialog" aria-label="声音输出">
          <div className="agent-options" role="radiogroup">
            {routes.map(route => {
              const selected = route === (state?.audio_preferred ?? output)
              return (
                <button key={route} type="button" role="radio" aria-checked={selected} className={selected ? 'selected' : ''}
                  onClick={() => {
                    if (route === 'browser' && output === 'browser') { if (!ready || !audible) toggle() }
                    else if (!selected) choose(route)
                    setOpen(false)
                  }}>
                  <Volume2 size={15} aria-hidden="true" />
                  <span><strong>{routeLabel(route, state)}</strong><small>{routeDetail(route, state)}</small></span>
                  {selected && <Check size={14} aria-hidden="true" />}
                </button>
              )
            })}
          </div>
          {output === 'browser' && ready && (
            <button type="button" className="audio-mute" onClick={toggle}>
              {audible ? <VolumeX size={14} aria-hidden="true" /> : <Volume2 size={14} aria-hidden="true" />}
              {audible ? '静音浏览器音频' : '取消静音'}
            </button>
          )}
        </div>
      )}
    </div>
  )
}

function AudioRouteStatus({ state, ready, audible, toggle }: {
  state: State | null
  ready: boolean
  audible: boolean
  toggle: () => void
}) {
  const output = state?.audio_output ?? 'browser'
  const onFallback = state?.audio_preferred === 'supercollider' && output !== 'supercollider'
  const indicator = onFallback
    ? fallbackLabels[state.audio_reason ?? 'unavailable'] ?? fallbackLabels.unavailable
    : undefined

  if (output === 'supercollider') {
    if (state?.audio_fallback === 'browser' && !ready) {
      return (
        <button type="button" className="audio-toggle" onClick={toggle} aria-label="启用浏览器备用音源">
          <Volume2 size={17} aria-hidden="true" />
          <span className="audio-label"><span>SuperCollider</span><small>启用浏览器备用</small></span>
        </button>
      )
    }
    return <span className="audio-toggle" role="status"><Volume2 size={17} aria-hidden="true" />SuperCollider</span>
  }

  if (output === 'native') {
    return (
      <span className="audio-toggle" role="status">
        <Volume2 size={17} aria-hidden="true" />
        <span className="audio-label">
          <span>{state?.audio_device === 'logic' ? 'Logic Pro' : onFallback ? '备用 · FluidSynth' : '设备音频'}</span>
          {indicator && <small>{indicator}</small>}
        </span>
      </span>
    )
  }

  const enabled = audible && ready
  const label = !ready ? '启用音频' : enabled ? '浏览器音频' : '音频已静音'
  return (
    <button type="button" className="audio-toggle" onClick={toggle} aria-pressed={enabled}>
      {enabled ? <Volume2 size={17} aria-hidden="true" /> : <VolumeX size={17} aria-hidden="true" />}
      <span className="audio-label">
        <span>{onFallback && enabled ? '备用 · 浏览器音频' : label}</span>
        {indicator && <small>{indicator}</small>}
      </span>
    </button>
  )
}
