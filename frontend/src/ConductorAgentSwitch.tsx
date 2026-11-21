import { Bot, Check, Cloud, Cpu, KeyRound, ListChecks, Loader2 } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'

type AgentId = 'local' | 'cloud' | 'rules'

type Agents = {
  active: AgentId
  agents: { id: AgentId; label: string; model: string | null; available: boolean }[]
  cloud: { provider: string; base_url: string; model: string; key_hint: string | null }
  providers: Record<string, { label: string; base_url: string; model: string }>
}

type TestResult = { ok: boolean; reason: string | null; model: string | null }

const icons = { local: Cpu, cloud: Cloud, rules: ListChecks }

const reasons: Record<string, string> = {
  rejected: '服务商拒绝了请求：检查 key、模型名和账户余额',
  timeout: '超时：网络或服务商响应太慢',
  unavailable: '连不上服务商，或对方暂时不可用',
  invalid_response: '能连上，但返回的不是有效 JSON：换一个模型试试',
  busy: '指挥正在处理别的请求，稍后再试',
  not_configured: '还没有保存 key',
}

async function call(path: string, method = 'GET', body?: object) {
  const response = await fetch(path, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })
  const result = await response.json().catch(() => ({}))
  if (!response.ok) {
    const detail = result.detail
    throw new Error(typeof detail === 'string' ? detail : detail?.[0]?.msg ?? `请求失败（${response.status}）`)
  }
  return result
}

export function ConductorAgentSwitch() {
  const [agents, setAgents] = useState<Agents | null>(null)
  const [open, setOpen] = useState(false)
  const [provider, setProvider] = useState('deepseek')
  const [baseUrl, setBaseUrl] = useState('')
  const [model, setModel] = useState('')
  const [key, setKey] = useState('')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null)
  const root = useRef<HTMLDivElement>(null)

  function load(next: Agents) {
    setAgents(next)
    setProvider(next.cloud.provider)
    setBaseUrl(next.cloud.base_url)
    setModel(next.cloud.model)
  }

  useEffect(() => { call('/conductor/agents').then(load).catch(() => setAgents(null)) }, [])

  useEffect(() => {
    if (!open) return
    const close = (event: PointerEvent | KeyboardEvent) => {
      if (event instanceof KeyboardEvent ? event.key === 'Escape' : !root.current?.contains(event.target as Node)) setOpen(false)
    }
    document.addEventListener('pointerdown', close)
    document.addEventListener('keydown', close)
    return () => { document.removeEventListener('pointerdown', close); document.removeEventListener('keydown', close) }
  }, [open])

  async function run(action: () => Promise<void>) {
    setBusy(true)
    setMessage(null)
    try { await action() } catch (error) {
      setMessage({ ok: false, text: error instanceof Error ? error.message : String(error) })
    } finally { setBusy(false) }
  }

  if (!agents) return null
  const active = agents.agents.find(agent => agent.id === agents.active) ?? agents.agents[2]
  const ActiveIcon = icons[active.id]
  const preset = agents.providers[provider]

  const choose = (id: AgentId) => run(async () => load(await call('/conductor/agents', 'PATCH', { id })))
  const save = () => run(async () => {
    load(await call('/conductor/cloud', 'PUT', { provider, base_url: baseUrl, model, api_key: key || null }))
    setKey('')
    setMessage({ ok: true, text: '已保存在本机，key 不会再显示' })
  })
  const test = () => run(async () => {
    if (key || baseUrl !== agents.cloud.base_url || model !== agents.cloud.model || provider !== agents.cloud.provider) {
      load(await call('/conductor/cloud', 'PUT', { provider, base_url: baseUrl, model, api_key: key || null }))
      setKey('')
    }
    const result: TestResult = await call('/conductor/cloud/test', 'POST')
    setMessage(result.ok
      ? { ok: true, text: `连接成功 · ${result.model ?? model}` }
      : { ok: false, text: reasons[result.reason ?? ''] ?? '连接失败' })
  })

  return (
    <div className="agent-switch" ref={root}>
      <button type="button" className="audio-toggle agent-button" aria-expanded={open} aria-haspopup="dialog"
        onClick={() => setOpen(value => !value)} title="切换指挥 agent">
        <ActiveIcon size={16} aria-hidden="true" />
        <span className="audio-label">
          <span>指挥 · {active.label}</span>
          <small>{active.model ?? (active.id === 'rules' ? '不调用模型' : '未配置')}</small>
        </span>
      </button>
      {open && (
        <div className="agent-popover" role="dialog" aria-label="指挥 agent">
          <h2><Bot size={15} aria-hidden="true" />谁来当指挥</h2>
          <div className="agent-options" role="radiogroup">
            {agents.agents.map(agent => {
              const Icon = icons[agent.id]
              const selected = agent.id === agents.active
              return (
                <button key={agent.id} type="button" role="radio" aria-checked={selected}
                  className={selected ? 'selected' : ''} disabled={busy || !agent.available}
                  onClick={() => !selected && choose(agent.id)}>
                  <Icon size={15} aria-hidden="true" />
                  <span><strong>{agent.label}</strong><small>{agent.model ?? (agent.id === 'rules' ? '只用内置规则' : agent.id === 'cloud' ? '先在下面保存 key' : '未启动')}</small></span>
                  {selected && <Check size={14} aria-hidden="true" />}
                </button>
              )
            })}
          </div>

          <form className="agent-cloud" onSubmit={event => { event.preventDefault(); save() }}>
            <h3><Cloud size={13} aria-hidden="true" />云端 API（OpenAI 兼容）</h3>
            <label>服务商
              <select value={provider} disabled={busy} onChange={event => {
                const next = agents.providers[event.target.value]
                setProvider(event.target.value)
                setBaseUrl(next.base_url)
                setModel(next.model)
              }}>
                {Object.entries(agents.providers).map(([id, entry]) => <option key={id} value={id}>{entry.label}</option>)}
              </select>
            </label>
            <label>模型
              <input value={model} disabled={busy} placeholder={preset?.model || 'model-name'} spellCheck={false}
                onChange={event => setModel(event.target.value)} />
            </label>
            <label className="wide">API 地址
              <input value={baseUrl} disabled={busy} placeholder="https://…/v1" spellCheck={false}
                onChange={event => setBaseUrl(event.target.value)} />
            </label>
            <label className="wide"><span><KeyRound size={12} aria-hidden="true" /> API key</span>
              <input type="password" value={key} disabled={busy} autoComplete="off" spellCheck={false}
                placeholder={agents.cloud.key_hint ? `已保存 ${agents.cloud.key_hint}，留空则保留` : '在这里粘贴 key（⌘V）'}
                onChange={event => setKey(event.target.value)} />
            </label>
            <div className="agent-actions wide">
              <button type="submit" disabled={busy}>保存</button>
              <button type="button" disabled={busy || (!key && !agents.cloud.key_hint)} onClick={test}>
                {busy ? <Loader2 size={13} className="spin" aria-hidden="true" /> : null}测试连接
              </button>
            </div>
            {message && <p className={`agent-message wide ${message.ok ? 'ok' : 'error'}`} role="status">{message.text}</p>}
            <p className="agent-note wide">key 只保存在本机 .runtime/llm/cloud.json（仅你的账户可读），不会发回浏览器。</p>
          </form>
        </div>
      )}
    </div>
  )
}
