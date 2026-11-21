import { Volume2, VolumeX } from 'lucide-react'
import type { State } from './model'

const fallbackLabels: Record<string, string> = {
  starting: 'SuperCollider 连接中',
  unavailable: 'SuperCollider 暂不可用',
  disconnected: 'SuperCollider 已断开',
}

export function AudioRoute({ state, ready, audible, toggle }: {
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
