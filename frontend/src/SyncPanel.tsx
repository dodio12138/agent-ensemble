import { useState } from 'react'
import { Cable, Check, Copy, Gauge, Link2, Network } from 'lucide-react'
import { TRACK_INFO, trackLabel, tracksFor, type LinkPatch, type State } from './model'

function LinkSection({ state, disabled, onChange }: {
  state: State
  disabled: boolean
  onChange: (values: LinkPatch) => void
}) {
  const link = state.link
  if (!link) return null
  const quantum = link.quantum ?? 4
  const beats = Math.max(1, Math.round(quantum))
  const current = link.phase != null ? Math.floor(link.phase) : -1
  const peers = link.peers ?? 0
  const waiting = state.timing?.waiting_for_downbeat
  return (
    <section className="sync-section" aria-labelledby="link-heading">
      <header>
        <h3 id="link-heading"><Link2 size={15} aria-hidden="true" />Ableton Link</h3>
        <label className="sync-switch">
          <input type="checkbox" aria-label="加入 Ableton Link" checked={link.enabled} disabled={disabled || !link.available}
            onChange={event => onChange({ enabled: event.target.checked })} />
          <span>{link.enabled ? '已加入' : '未加入'}</span>
        </label>
      </header>
      {!link.available ? (
        <p className="sync-note">未安装 Link 组件。运行 <code>./scripts/setup</code> 后重启。</p>
      ) : link.enabled ? (
        <>
          <div className="link-meter" role="img" aria-label={`Link 相位 ${link.phase ?? 0} / ${quantum} 拍`}>
            {Array.from({ length: beats }, (_, beat) => (
              <span key={beat} className={beat === current ? 'on' : ''}>{beat + 1}</span>
            ))}
          </div>
          <dl className="sync-stats">
            <div><dt>同伴</dt><dd className={peers ? 'good' : ''}>{peers ? `${peers} 个应用` : '等待中'}</dd></div>
            <div><dt>共享速度</dt><dd>{link.tempo?.toFixed(1)} BPM</dd></div>
            <div><dt>小节长度</dt><dd>{quantum} 拍</dd></div>
            <div><dt>相位校正</dt><dd>{link.corrections ? `${link.last_shift_ms} ms · ${link.corrections} 次` : '无需校正'}</dd></div>
          </dl>
          <label className="sync-check">
            <input type="checkbox" aria-label="同步启停" checked={link.start_stop} disabled={disabled}
              onChange={event => onChange({ start_stop: event.target.checked })} />
            同步启停（其他应用按播放时乐队一起开始）
          </label>
          {waiting && <p className="sync-note accent">等待共享小节起点后开始演奏</p>}
        </>
      ) : (
        <p className="sync-note">与同一网络中的 Ableton Live、Bitwig、手机应用等共享速度和小节相位。播放会在共享的小节起点开始。</p>
      )}
      {link.error && <p className="sync-note error">{link.error}</p>}
    </section>
  )
}

function CopyCommand({ command }: { command: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <div className="sync-command">
      <code>{command}</code>
      <button type="button" aria-label="复制命令" title="复制命令" onClick={() => {
        void navigator.clipboard?.writeText(command).then(() => {
          setCopied(true)
          setTimeout(() => setCopied(false), 1400)
        })
      }}>{copied ? <Check size={14} /> : <Copy size={14} />}</button>
    </div>
  )
}

function RemoteSection({ state }: { state: State }) {
  const lineup = state.settings.lineup ?? 'trio'
  const agents = state.remote_agents ?? {}
  const port = window.location.port || '8765'
  const command = `.venv/bin/python -m scripts.remote_agent --track bass --server http://127.0.0.1:${port} --session ${state.id}`
  return (
    <section className="sync-section" aria-labelledby="remote-heading">
      <header>
        <h3 id="remote-heading"><Network size={15} aria-hidden="true" />分布式乐手</h3>
        <span className="sync-meta">{Object.keys(agents).length} 个远程进程</span>
      </header>
      <ul className="remote-list">
        {tracksFor(lineup).map(track => {
          const agent = agents[track]
          const { name } = trackLabel(track, lineup)
          const missed = agent ? agent.late + agent.invalid : 0
          return (
            <li key={track} className={agent ? 'remote' : ''} style={{ '--accent': TRACK_INFO[track].color } as React.CSSProperties}>
              <span className="remote-track"><i />{name}</span>
              {agent ? (
                <span className="remote-detail">
                  <strong>{agent.name}</strong>
                  <small>
                    {agent.latency_ms != null ? `${agent.latency_ms} ms` : '等待首个乐句'}
                    {' · '}{agent.answered}/{agent.requests} 已采用
                    {missed > 0 && <em> · {missed} 次兜底</em>}
                  </small>
                </span>
              ) : <span className="remote-detail"><small>本机规则乐手</small></span>}
            </li>
          )
        })}
      </ul>
      <p className="sync-note">另一个进程可以通过 WebSocket 接管任意一轨。每个小节有 {agents.bass?.deadline_ms ?? 300} ms 的回复时限，迟到或无效时该小节由本机乐手补上。</p>
      <CopyCommand command={command} />
    </section>
  )
}

function TimingSection({ state }: { state: State }) {
  const timing = state.timing
  if (!timing) return null
  const format = (value: number | null) => value == null ? '—' : value >= 1000 ? `${(value / 1000).toFixed(1)} ms` : `${value} µs`
  return (
    <section className="sync-section" aria-labelledby="timing-heading">
      <header>
        <h3 id="timing-heading"><Gauge size={15} aria-hidden="true" />调度精度</h3>
        <span className={`sync-badge ${timing.engine}`}>
          <Cable size={12} aria-hidden="true" />
          {timing.engine === 'native' ? `原生计时${timing.promoted ? ' · 实时优先级' : ''}` : 'Python 计时'}
        </span>
      </header>
      <dl className="sync-stats three">
        <div><dt>中位延迟</dt><dd>{format(timing.p50_us)}</dd></div>
        <div><dt>P99</dt><dd>{format(timing.p99_us)}</dd></div>
        <div><dt>最大</dt><dd>{format(timing.max_us)}</dd></div>
      </dl>
      <p className="sync-note">统计最近 {timing.samples || 0} 个音符相对计划时间的唤醒延迟。</p>
    </section>
  )
}

export function SyncPanel({ state, disabled, onLink }: {
  state: State
  disabled: boolean
  onLink: (values: LinkPatch) => void
}) {
  return (
    <section className="panel sync-panel" aria-label="同步与分布式">
      <LinkSection state={state} disabled={disabled} onChange={onLink} />
      <RemoteSection state={state} />
      <TimingSection state={state} />
    </section>
  )
}
