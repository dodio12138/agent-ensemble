import { useRef, useState } from 'react'
import { FileMusic, Send } from 'lucide-react'
import {
  CORE_TRACK_IDS, GENRE_NAMES, GROOVE_NAMES, SECTION_NAMES, STYLE_NAMES, TRACK_INFO,
  type ConductorPlan, type HarmonicMove, type ImprovisedChorus, type ImprovisationPhrase,
  type MusicProfile, type Song, type State, type Style,
} from './model'

const approaches = {
  drums: { balanced: '均衡节奏', sparse: '简约节奏', driving: '推进节奏' },
  bass: { walking: '行进低音', root: '根音低音', syncopated: '切分低音' },
  piano: { comp: '和弦伴奏', sustained: '长音和弦', syncopated: '切分伴奏' },
} as const

const reasons: Record<string, string> = {
  score_required: '按原谱演奏需要在「MIDI 原谱」导入这首曲目的 MIDI 文件。',
  unrecognized_reference: '未能把这条参考转成可演奏的风格。可补充乐器、节奏或和声特征。',
  busy: '模型正忙',
  timeout: '模型超时',
  unavailable: '模型暂时不可用',
  invalid_response: '模型计划未通过校验',
  rejected: '模型请求被拒绝',
  unrecognized: '未匹配到本地指挥指令',
}

const characters: Record<ImprovisationPhrase['character'], string> = {
  space: '留白',
  answer: '回应',
  build: '推进',
  resolve: '收束',
}

const techniques: Record<HarmonicMove['technique'], string> = {
  color: '和弦加色', inversion: '转位低音', dominant: '副属和弦',
  borrowed: '调式借用', deceptive: '意外落点', tritone: '三全音替代',
}

const modes: Record<MusicProfile['mode'], string> = {
  major: '大调', minor: '自然小调', dorian: '多利亚调式', mixolydian: '混合利底亚调式',
}

const harmonies: Record<MusicProfile['harmony'], string> = {
  keep: '保留主题',
  pop_1564: 'I–V–vi–IV',
  minor_1645: 'i–♭VI–iv–V',
  mixolydian_174: 'I–♭VII–IV',
  blues_8: '八小节布鲁斯（I7、IV7、V7）',
  dorian_vamp: 'i7–IV7',
  minor_1767: 'i–♭VII–♭VI–♭VII',
  soul_251: 'ii7–V7–Imaj7',
  major_145: 'I–IV–V',
}

const examples = [
  { label: '摇滚', instruction: '摇滚' },
  { label: '披头士', instruction: '参考披头士的风格' },
  { label: '雷鬼', instruction: '雷鬼' },
  { label: '星际穿越曲风', instruction: '星际穿越曲风' },
  { label: '变拍与速度', instruction: '换成 6/8 拍，速度 108 BPM' },
]

const songExample = '写一首无歌词的流行摇滚器乐曲。短引子，A 段克制，B 段换成 6/8 拍，尾声回 4/4。'
const motifNames = { rise: '上行', fall: '下行', arch: '拱形', repeat: '重复' } as const
const textureNames = { sparse: '留白', full: '全乐队', answer: '问答' } as const
const keysToneNames = { piano: '钢琴', electric: '电钢琴', vibes: '颤音琴', organ: '管风琴' } as const

function directionName(plan: ConductorPlan) {
  return GENRE_NAMES[plan.profile?.genre ?? plan.style]
}

function sameProfile(left: MusicProfile | null | undefined, right: MusicProfile | null | undefined) {
  if (!left || !right) return !left && !right
  return left.genre === right.genre && left.groove === right.groove && left.mode === right.mode
    && left.harmony === right.harmony && left.tonic === right.tonic && left.basis === right.basis
    && left.reference === right.reference
}

function DirectionPreview({ plan, progression, startBar, currentBar }: {
  plan: ConductorPlan
  progression: string[]
  startBar?: number
  currentBar?: number
}) {
  const { profile } = plan
  const status = startBar === undefined ? '当前编排' : `第 ${startBar + 1} 小节生效`
  const reference = profile?.reference
  return (
    <article className={startBar === undefined ? 'direction-preview' : 'direction-preview queued'} aria-label={`${status}风格与和声`}>
      <header>
        <span>{status}</span>
        <strong>{directionName(plan)}</strong>
        {profile?.basis === 'reference' && reference && <span>{profile.genre === 'cinematic' ? '配乐参考' : '乐队参考'} {reference}</span>}
        {profile?.basis === 'inferred' && <span>风格推断{reference && ` · ${reference}`}</span>}
      </header>
      {profile && (
        <dl className="direction-traits">
          <div><dt>拍感</dt><dd>{GROOVE_NAMES[profile.groove]}</dd></div>
          {profile.harmony !== 'keep' && (
            <div><dt>调性</dt><dd>{profile.tonic.replace('#', '♯').replace('b', '♭')} {modes[profile.mode]}</dd></div>
          )}
          <div><dt>和声倾向</dt><dd>{harmonies[profile.harmony]}</dd></div>
        </dl>
      )}
      <ol className="chorus-chords" aria-label="主题和弦">
        {progression.map((chord, index) => (
          <li key={index} aria-current={currentBar === index ? 'step' : undefined}>
            <span>{index + 1}</span><strong>{chord}</strong>
          </li>
        ))}
      </ol>
    </article>
  )
}

function ChorusPreview({ chorus, currentBar, transport }: { chorus: ImprovisedChorus; currentBar?: number; transport?: State['transport'] }) {
  const { plan, start_bar: start, end_bar: end } = chorus
  const length = plan.progression.length
  const playedBars = end - start
  const offset = plan.relative_to_start ? 0 : start % length
  const progression = plan.progression.slice(0, playedBars).map((_, index) => plan.progression[(offset + index) % length])
  const moves = (plan.harmonic_moves ?? []).map(move => ({
    ...move,
    bar: start + (move.position - offset + length) % length,
  })).filter(move => move.bar < end).sort((left, right) => left.bar - right.bar)
  const playing = transport === 'playing'
  const paused = transport === 'paused'
  return (
    <article className="chorus-preview" aria-label={playing ? '正在演奏的即兴' : paused ? '已暂停的即兴' : '待演奏即兴'}>
      <header>
        <strong>{playing ? '演奏中' : paused ? '已暂停' : '待演奏'}</strong>
        <span>第 {start + 1}–{end} 小节</span>
        <span className={chorus.source === 'llm' ? 'conductor-llm' : 'conductor-rules'} title={chorus.model ?? undefined}>
          {chorus.source === 'llm' ? 'LLM 指挥' : '本地规则'}
        </span>
      </header>
      <ol className="chorus-chords" aria-label="即兴和弦">
        {progression.map((chord, index) => (
          <li key={index} aria-current={currentBar === start + index ? 'step' : undefined}>
            <span>{start + index + 1}</span><strong>{chord}</strong>
          </li>
        ))}
      </ol>
      {moves.length > 0 && (
        <ol className="chorus-phrases" aria-label="即兴和声变化">
          {moves.map(move => (
            <li key={`${move.position}-${move.technique}-${move.chord}`} className={currentBar === move.bar ? 'current' : undefined}
              aria-current={currentBar === move.bar ? 'step' : undefined}>
              <span>第 {move.bar + 1} 小节</span>
              <strong>{move.chord}</strong>
              <span>{techniques[move.technique]}</span>
            </li>
          ))}
        </ol>
      )}
      <ol className="chorus-phrases" aria-label="每两小节的领奏安排">
        {plan.phrases.slice(0, Math.ceil(playedBars / 2)).map((phrase, index) => {
          const phraseBar = start + index * 2
          const phraseEnd = Math.min(phraseBar + 2, end)
          const current = currentBar !== undefined && currentBar >= phraseBar && currentBar < phraseEnd
          return (
            <li key={index} className={current ? 'current' : undefined} aria-current={current ? 'step' : undefined}>
              <span>{phraseEnd === phraseBar + 1 ? phraseEnd : `${phraseBar + 1}–${phraseEnd}`}</span>
              <strong style={{ color: TRACK_INFO[phrase.lead].color }}>{TRACK_INFO[phrase.lead].name}领奏</strong>
              <span>{characters[phrase.character]}</span>
            </li>
          )
        })}
      </ol>
    </article>
  )
}

function SongRoadmap({ song, state, startBar, pending }: {
  song: Song
  state: State
  startBar?: number | null
  pending: boolean
}) {
  let cursor = startBar ?? 0
  const total = song.sections.reduce((bars, section) => bars + section.bars, 0)
  const source = state.composition?.source
  return (
    <article className="song-roadmap" aria-label="全曲段落">
      <header>
        <strong>{total} 小节</strong>
        <span>{song.tonic.replace('#', '♯').replace('b', '♭')} {modes[song.mode]}</span>
        {pending && startBar != null && <span>第 {startBar + 1} 小节起</span>}
        {source && (
          <span className={source === 'llm' ? 'conductor-llm' : 'conductor-rules'}>
            {source === 'llm' ? 'LLM 指挥' : '本地规则'}
          </span>
        )}
      </header>
      <ol className="song-sections">
        {song.sections.map((section, index) => {
          const previous = song.sections[index - 1]
          const begin = cursor
          cursor += section.bars
          const current = !pending && state.section?.index === index
          const changes = [
            section.bpm !== previous?.bpm ? `${section.bpm} BPM` : '',
            section.meter !== previous?.meter ? `${section.meter} 拍` : '',
          ].filter(Boolean).join(' · ')
          return (
            <li className={current ? 'current' : undefined} aria-current={current ? 'step' : undefined}
              key={index}>
              <div>
                <strong>{SECTION_NAMES[section.role]}</strong>
                <span>{begin + 1}–{cursor}</span>
              </div>
              {changes && <span className="song-change">{changes}</span>}
              <span className="song-harmony" title={section.progression.join(' · ')}>
                {section.progression.join(' · ')}
              </span>
              <span className="song-character">{[
                motifNames[section.motif], textureNames[section.texture],
                section.keys_tone ? keysToneNames[section.keys_tone] : '',
                section.mix === 'lead' ? '旋律突出' : section.mix === 'rhythm' ? '鼓贝斯突出' : '',
              ].filter(Boolean).join(' · ')}</span>
            </li>
          )
        })}
      </ol>
    </article>
  )
}

export function ConductorPanel({ state, disabled, conducting, improvising, composing, error, onConduct, onPreset, onImprovise, onAuto, onCompose, onLeaveSong }: {
  state: State | null
  disabled: boolean
  conducting: boolean
  improvising: boolean
  composing: boolean
  error: string
  onConduct: (instruction: string) => void
  onPreset: (style: Style) => void
  onImprovise: (instruction: string) => void
  onAuto: (auto: boolean) => void
  onCompose: (instruction: string) => void
  onLeaveSong: () => void
}) {
  const [instruction, setInstruction] = useState('')
  const input = useRef<HTMLInputElement>(null)
  const active = state?.settings.conductor
  const result = state?.conductor
  const pending = state?.pending_changes ?? (state?.pending ? [state.pending] : [])
  const next = pending.at(-1)
  const plan = next?.settings.conductor ?? active
  const currentProgression = state?.settings.progression ?? []
  const staged = pending.find(change => {
    const profile = change.settings.conductor?.profile
    return (profile || active?.profile) && (!sameProfile(active?.profile, profile)
      || change.settings.progression.some((chord, index) => chord !== currentProgression[index]))
  })
  const upcoming = staged?.settings.conductor
  const bar = result?.effective_bar == null ? null : result.effective_bar + 1
  const source = result?.source === 'llm' ? 'LLM 指挥' : result?.source === 'preset' ? '风格预设'
    : result?.reason && result.reason !== 'not_configured' ? '规则兜底' : '本地规则'
  const reason = result?.reason ? reasons[result.reason] : undefined
  const timing = result && [
    result.bpm != null ? `${result.bpm} BPM` : '',
    result.meter ? `${result.meter} 拍` : '',
  ].filter(Boolean).join(' · ')
  const improvisation = state?.improvisation
  const planning = improvising || improvisation?.planning
  const composition = state?.composition
  const composingSong = composing || composition?.planning
  const requestInFlight = conducting || planning || composingSong
  const newSong = next?.settings.song && pending.find(change => change.settings.song
    && (composition?.start_bar === change.bar || !state?.settings.song))
  const song = newSong?.settings.song ?? state?.settings.song
  const songStart = newSong?.bar ?? state?.song_origin ?? composition?.start_bar
  const leaving = song && next && !next.settings.song ? next.bar : null
  const pendingPreset = next?.settings.conductor && !next.settings.conductor.profile ? next.settings.conductor.style : null
  const activePreset = active && !active.profile ? active.style : null
  const currentChord = state && state.transport !== 'stopped' && !improvisation?.active
    ? state.bar % currentProgression.length : undefined

  return (
    <section className="panel conductor" aria-label="指挥">
      <header className="panel-heading">
        <h2><FileMusic aria-hidden="true" size={17} />指挥</h2>
        {active && (
          <div className="conductor-current">
            <strong>{directionName(active)}</strong>
            <span>强度 <b>{active.intensity}%</b></span>
          </div>
        )}
      </header>
      <div className="conductor-body">
        {improvisation && (
          <div className="style-presets" role="group" aria-label="风格预设">
            {(Object.keys(STYLE_NAMES) as Style[]).map(style => {
              const queued = pendingPreset === style && activePreset !== style
              return (
                <button key={style} type="button" className={queued ? 'queued' : undefined}
                  aria-pressed={activePreset === style} disabled={disabled || composingSong} onClick={() => onPreset(style)}>
                  {STYLE_NAMES[style]}{queued && <small>待生效</small>}
                </button>
              )
            })}
          </div>
        )}
        <form className="conductor-form" onSubmit={event => {
          event.preventDefault()
          if (!disabled && !requestInFlight && instruction.trim()) onConduct(instruction.trim())
        }}>
          <input
            ref={input}
            aria-label="指挥指令"
            placeholder="输入风格、和声、速度或拍号"
            type="text"
            spellCheck={false}
            maxLength={500}
            value={instruction}
            onChange={event => setInstruction(event.target.value)}
            onKeyDown={event => {
              if (event.key === 'Enter' && event.nativeEvent.isComposing) event.preventDefault()
            }}
            disabled={disabled || requestInFlight}
          />
          <button type="submit" disabled={disabled || requestInFlight || !instruction.trim()}>
            {!conducting && <Send size={15} aria-hidden="true" />}
            {conducting ? '编排中…' : '提交指令'}
          </button>
        </form>
        <div className="conductor-examples" role="group" aria-label="指挥指令示例">
          <span>试试</span>
          {examples.map(example => (
            <button key={example.label} type="button" disabled={disabled || requestInFlight}
              onClick={() => {
                setInstruction(example.instruction)
                input.current?.focus()
              }}>
              {example.label}
            </button>
          ))}
          {composition && (
            <button type="button" disabled={disabled || requestInFlight} onClick={() => {
              setInstruction(songExample)
              input.current?.focus()
            }}>
              器乐曲示例
            </button>
          )}
        </div>
        <div className={`conductor-result ${error ? 'error' : ''}`} role="status">
          {error || (conducting ? '正在生成编排，乐队照常演奏。' : result && (
            <>
              <span className={result.source === 'llm' ? 'conductor-llm' : 'conductor-rules'}
                title={result.model ?? undefined}>{source}</span>
              {result.status === 'scheduled' && (
                <span>
                  <strong>第 {bar} 小节生效</strong> · {directionName(result.plan)} · 强度 {result.plan.intensity}%
                  {timing && ` · ${timing}`}
                </span>
              )}
              {result.status === 'applied' && (
                <span>{bar === null ? '编排已生效' : `第 ${bar} 小节已生效`}{timing && ` · ${timing}`}</span>
              )}
              {result.status === 'unchanged' && <span>当前编排保持不变</span>}
              {reason && <span className="conductor-reason">{reason}</span>}
            </>
          ))}
        </div>
        {active && (active.profile || upcoming?.profile) && (
          <div className="direction-views">
            <DirectionPreview plan={active} progression={currentProgression} currentBar={currentChord} />
            {staged && upcoming && (
              <DirectionPreview plan={upcoming} progression={staged.settings.progression} startBar={staged.bar} />
            )}
          </div>
        )}
        {plan && (
          <dl className="conductor-intents" aria-label={next?.settings.conductor === plan ? '待生效乐手意图' : '当前乐手意图'}>
            {CORE_TRACK_IDS.map(track => (
              <div key={track}>
                <dt style={{ color: TRACK_INFO[track].color }}>{TRACK_INFO[track].name}</dt>
                <dd>
                  {track === 'drums' && approaches.drums[plan.drums]}
                  {track === 'bass' && approaches.bass[plan.bass]}
                  {track === 'piano' && approaches.piano[plan.piano]}
                </dd>
              </div>
            ))}
          </dl>
        )}
        {composition && (
          <section className="conductor-improvisation" aria-labelledby="composition-heading">
            <header>
              <h3 id="composition-heading">全曲编排</h3>
              <div className="improvisation-actions">
                {song && (
                  <button type="button" disabled={disabled || requestInFlight || leaving !== null}
                    onClick={onLeaveSong}>
                    退出歌曲
                  </button>
                )}
                <button type="button" disabled={disabled || requestInFlight || !instruction.trim()}
                  onClick={() => onCompose(instruction.trim())}>
                  {composingSong ? '编排中…' : '编排整首'}
                </button>
              </div>
            </header>
            <p className="improvisation-state" role="status">
              {composingSong ? '正在安排段落，乐队照常演奏。'
                : composition.error || (leaving !== null ? `第 ${leaving + 1} 小节回到八小节循环`
                  : !song ? '描述整首曲子，再点「编排整首」。' : '')}
            </p>
            {song && <SongRoadmap song={song} state={state} startBar={songStart} pending={!!newSong} />}
          </section>
        )}
        {improvisation && (
          <section className="conductor-improvisation" aria-labelledby="improvisation-heading">
            <header>
              <h3 id="improvisation-heading">指挥即兴</h3>
              <div className="improvisation-actions">
                <label>
                  <input type="checkbox" checked={improvisation.auto} disabled={disabled}
                    onChange={event => onAuto(event.target.checked)} />
                  自动即兴
                </label>
                <button type="button" disabled={disabled || requestInFlight} onClick={() => onImprovise(instruction.trim())}>
                  来一轮即兴
                </button>
                <button type="button" disabled={disabled || requestInFlight} onClick={() => onImprovise('来一点反套路的即兴')}>
                  反套路即兴
                </button>
              </div>
            </header>
            <p className="improvisation-state" role="status">
              {planning ? '正在安排下一轮即兴…' : improvisation.last_error && (reasons[improvisation.last_error] || improvisation.last_error)}
            </p>
            {improvisation.active && (
              <ChorusPreview chorus={improvisation.active} currentBar={state?.transport === 'stopped' ? undefined : state?.bar}
                transport={state?.transport} />
            )}
            {improvisation.pending && <ChorusPreview chorus={improvisation.pending} />}
          </section>
        )}
      </div>
    </section>
  )
}
