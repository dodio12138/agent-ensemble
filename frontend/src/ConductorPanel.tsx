import { useRef, useState } from 'react'
import { FileMusic, Send } from 'lucide-react'
import {
  CORE_TRACK_IDS, GENRE_NAMES, GROOVE_NAMES, SECTION_NAMES, TRACK_INFO, soloistFor,
  type ConductorPlan, type Genre, type HarmonicMove, type ImprovisedChorus, type ImprovisationPhrase,
  type JamPatch, type Lineup, type MusicProfile, type Song, type State,
} from './model'

// Every style the band can play, grouped by family. Each applies at once with local rules.
const STYLE_GROUPS: { label: string; styles: Genre[] }[] = [
  { label: '爵士 · 拉丁', styles: ['swing', 'bossa', 'latin'] },
  { label: '律动', styles: ['funk', 'disco', 'soul', 'hip_hop', 'reggae'] },
  { label: '摇滚 · 布鲁斯', styles: ['rock', 'hard_rock', 'alternative_rock', 'blues', 'country'] },
  { label: '流行 · 抒情', styles: ['pop', 'ballad'] },
  { label: '配乐 · 电子', styles: ['cinematic', 'ambient', 'electronic', 'classical'] },
  { label: '游戏配乐', styles: ['chiptune', 'platformer', 'rpg', 'battle', 'exploration', 'stealth'] },
]

function genreOf(plan?: ConductorPlan | null): Genre | null {
  return plan ? plan.profile?.genre ?? plan.style : null
}

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
  not_configured: '未加载本地模型',
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

type Mode = 'adjust' | 'improvise' | 'compose'

const MODES: Record<Mode, { label: string; placeholder: string; submit: string; busy: string }> = {
  adjust: { label: '调整编排', placeholder: '风格、和声、速度、拍号或强度', submit: '提交', busy: '编排中…' },
  improvise: { label: '即兴一轮', placeholder: '可留空，或写下这轮即兴的想法', submit: '开始即兴', busy: '安排中…' },
  compose: { label: '编排整首', placeholder: '描述整首曲子的段落、拍号与速度', submit: '编排整首', busy: '编排中…' },
}

const EXAMPLES: Record<Mode, { label: string; instruction: string }[]> = {
  adjust: [
    { label: '变拍与速度', instruction: '换成 6/8 拍，速度 108 BPM' },
    { label: '强度', instruction: '强度 80%' },
    { label: '乐队参考', instruction: '参考披头士的风格' },
    { label: '中国摇滚', instruction: '像唐朝乐队那样的硬摇滚' },
    { label: '影视配乐', instruction: '星际穿越曲风' },
  ],
  improvise: [
    { label: '贝斯先答', instruction: '贝斯先答一句，留出空间，再回主题' },
    { label: '反套路', instruction: '来一点反套路的即兴' },
    { label: '副属和弦', instruction: '试一下副属和弦' },
    { label: '保留原和弦', instruction: '保留原和弦' },
  ],
  compose: [
    { label: '器乐曲示例', instruction: '写一首无歌词的流行摇滚器乐曲。短引子，A 段克制，B 段换成 6/8 拍，尾声回 4/4。' },
    { label: '变拍副歌', instruction: 'E 大调流行摇滚器乐，主歌 4/4，副歌转成 6/8 和 132 BPM，尾声回 4/4，突出旋律' },
  ],
}

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

function traits(plan: ConductorPlan) {
  const profile = plan.profile
  if (!profile) return ''
  return [
    GROOVE_NAMES[profile.groove],
    profile.harmony !== 'keep' ? `${profile.tonic.replace('#', '♯').replace('b', '♭')} ${modes[profile.mode]}` : '',
    harmonies[profile.harmony],
    profile.basis === 'reference' && profile.reference ? `参考 ${profile.reference}` : '',
  ].filter(Boolean).join(' · ')
}

function ChorusPreview({ chorus, currentBar, transport, lineup }: {
  chorus: ImprovisedChorus
  currentBar?: number
  transport?: State['transport']
  lineup?: Lineup
}) {
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
        {chorus.source !== 'llm' && chorus.reason && reasons[chorus.reason] && (
          <span className="conductor-reason" title={chorus.reason === 'timeout' ? '电脑负载较高时，本地模型可能来不及在 24 秒内完成' : undefined}>
            {reasons[chorus.reason]}
          </span>
        )}
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
              {(() => {
                // In the full bands the lead voice takes the melodic solos.
                const soloist = soloistFor(phrase.lead, lineup)
                return <strong style={{ color: TRACK_INFO[soloist].color }}>{TRACK_INFO[soloist].name}领奏</strong>
              })()}
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

export function ConductorPanel({ state, disabled, conducting, improvising, composing, error, onConduct, onPreset, onImprovise, onAuto, onCompose, onLeaveSong, onJam }: {
  state: State | null
  disabled: boolean
  conducting: boolean
  improvising: boolean
  composing: boolean
  error: string
  onConduct: (instruction: string) => void
  onPreset: (style: Genre) => void
  onImprovise: (instruction: string) => void
  onAuto: (auto: boolean) => void
  onCompose: (instruction: string) => void
  onLeaveSong: () => void
  onJam: (values: JamPatch) => void
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
  const activeGenre = genreOf(active)
  const pendingGenre = genreOf(next?.settings.conductor)

  const [mode, setMode] = useState<Mode>('adjust')
  const queuedTiming = next && state && [
    next.settings.bpm !== state.settings.bpm ? `${next.settings.bpm} BPM` : '',
    next.settings.meter && next.settings.meter !== state.settings.meter ? `${next.settings.meter} 拍` : '',
  ].filter(Boolean).join(' · ')
  const queuedStyle = upcoming && active && genreOf(upcoming) !== genreOf(active) ? directionName(upcoming) : ''
  const queued = staged || (next && queuedTiming) ? [
    queuedStyle, queuedTiming, staged && !queuedStyle ? '换和声' : '',
  ].filter(Boolean).join(' · ') : ''
  const queuedBar = (staged ?? next)?.bar
  const jam = state?.jam
  const playingMode = jam?.enabled ? `即兴演出${jam.current ? ` · ${jam.current.label}` : ''}`
    : improvisation?.active ? '即兴中'
    : song ? `整首歌${state?.section ? ` · ${SECTION_NAMES[state.section.role]}` : ''}` : '八小节循环'
  const refinement = state?.refinement
  const refiningName = refinement ? GENRE_NAMES[refinement.genre as Genre] ?? refinement.genre : ''
  const refinementNote = refinement && refinement.status !== 'planning' && refinement.status !== 'applied'
    ? refinement.status === 'superseded' ? '' : `LLM 未细化${refiningName}（${
      refinement.reason === 'jam' ? '即兴演出中' : refinement.reason === 'song' ? '整首歌由段落控制'
        : reasons[refinement.reason ?? ''] ?? '未完成'}），保留规则预设`
    : ''
  const busyText = refinement?.status === 'planning'
    ? `正在让 LLM 细化${refiningName}编配（规则预设已生效）`
    : conducting ? '正在生成编排，乐队照常演奏。'
    : composingSong ? '正在安排段落，乐队照常演奏。'
    : planning ? '正在安排下一轮即兴…' : ''
  const failure = error || composition?.error
    || (improvisation?.last_error ? reasons[improvisation.last_error] || improvisation.last_error : '')
  const canSubmit = !disabled && !requestInFlight && (mode === 'improvise' || !!instruction.trim())
  const modeInfo = MODES[mode]

  function submit() {
    if (!canSubmit) return
    const text = instruction.trim()
    if (mode === 'adjust') onConduct(text)
    else if (mode === 'improvise') onImprovise(text)
    else onCompose(text)
  }

  return (
    <section className="panel conductor" aria-label="指挥">
      <header className="panel-heading">
        <h2><FileMusic aria-hidden="true" size={17} />指挥</h2>
        <span className="conductor-mode-badge">{playingMode}{improvisation?.auto ? ' · 自动即兴' : ''}</span>
      </header>
      <div className="conductor-body">
        {active && (
          <section className="conductor-now" aria-label="当前编排">
            <div className="now-main">
              <strong>{directionName(active)}</strong>
              <span>强度 <b>{active.intensity}%</b></span>
              {traits(active) && <span className="now-traits">{traits(active)}</span>}
            </div>
            {jam?.enabled && jam.next ? (
              <p className="now-next">第 {jam.next.start_bar + 1} 小节起 → {jam.next.label}</p>
            ) : queued && queuedBar != null && (
              <p className="now-next">第 {queuedBar + 1} 小节起 → {queued}</p>
            )}
            <p className={`now-status ${failure ? 'error' : ''}`} role="status">
              {busyText || failure || (result ? (
                <>
                  <span className={result.source === 'llm' ? 'conductor-llm' : 'conductor-rules'}
                    title={result.model ?? undefined}>{source}</span>
                  {result.status === 'scheduled' && <span>第 {bar} 小节生效{timing && ` · ${timing}`}</span>}
                  {result.status === 'applied' && <span>{bar === null ? '已生效' : `第 ${bar} 小节已生效`}{timing && ` · ${timing}`}</span>}
                  {result.status === 'unchanged' && <span>编排保持不变</span>}
                  {reason && <span className="conductor-reason">{reason}</span>}
                </>
              ) : <span className="conductor-reason">选择风格或输入指令</span>)}
            </p>
            {!busyText && result?.source === 'llm' && result.note && (
              <p className="now-note" title={result.model ?? undefined}>“{result.note}”
                {result.corrections && result.corrections.length > 0 && (
                  <small> · 规则修正：{result.corrections.join('；')}</small>
                )}
              </p>
            )}
            {!busyText && refinementNote && <p className="now-note muted">{refinementNote}</p>}
            {plan && (
              <dl className="now-band" aria-label={next?.settings.conductor === plan ? '待生效乐手分工' : '当前乐手分工'}>
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
          </section>
        )}

        <div className="style-groups" role="group" aria-label="风格">
          {STYLE_GROUPS.map(group => (
            <div className="style-group" key={group.label}>
              <span>{group.label}</span>
              <div>
                {group.styles.map(style => {
                  const isQueued = pendingGenre === style && activeGenre !== style
                  return (
                    <button key={style} type="button" className={isQueued ? 'queued' : undefined}
                      aria-pressed={activeGenre === style} title={isQueued ? '待生效' : undefined}
                      disabled={disabled || composingSong} onClick={() => onPreset(style)}>
                      {GENRE_NAMES[style]}
                    </button>
                  )
                })}
              </div>
            </div>
          ))}
        </div>

        <section className="conductor-composer" aria-label="指令">
          <div className="composer-modes" role="tablist" aria-label="指令类型">
            {(Object.keys(MODES) as Mode[])
              .filter(key => key !== 'compose' || composition)
              .filter(key => key !== 'improvise' || improvisation)
              .map(key => (
                <button key={key} type="button" role="tab" aria-selected={mode === key}
                  onClick={() => setMode(key)}>
                  {MODES[key].label}
                </button>
              ))}
          </div>
          <form className="conductor-form" onSubmit={event => {
            event.preventDefault()
            submit()
          }}>
            <input
              ref={input}
              aria-label={`${modeInfo.label}指令`}
              placeholder={modeInfo.placeholder}
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
            <button type="submit" disabled={!canSubmit}>
              {!requestInFlight && <Send size={15} aria-hidden="true" />}
              {requestInFlight ? modeInfo.busy : modeInfo.submit}
            </button>
          </form>
          <div className="conductor-examples" role="group" aria-label="示例">
            {EXAMPLES[mode].map(example => (
              <button key={example.label} type="button" disabled={disabled || requestInFlight}
                onClick={() => {
                  setInstruction(example.instruction)
                  input.current?.focus()
                }}>
                {example.label}
              </button>
            ))}
            {mode === 'improvise' && improvisation && (
              <label className="composer-toggle" title="每轮八小节自动换和声并安排领奏">
                <input type="checkbox" checked={improvisation.auto} disabled={disabled || jam?.enabled}
                  onChange={event => onAuto(event.target.checked)} />
                自动即兴
              </label>
            )}
          </div>
          {mode === 'improvise' && jam && (
            <div className="jam-controls">
              <label className="jam-switch">
                <input type="checkbox" checked={jam.enabled} disabled={disabled}
                  onChange={event => onJam({ enabled: event.target.checked })} />
                <span>
                  <strong>即兴演出</strong>
                  <small>乐队自己轮流独奏、推进到高潮、变速、换律动、急停再进</small>
                </span>
              </label>
              <select aria-label="即兴演出风格" value={jam.profile} disabled={disabled}
                onChange={event => onJam({ profile: event.target.value })}>
                {Object.entries(jam.profiles).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </select>
            </div>
          )}
        </section>

        {(improvisation?.active || improvisation?.pending || song || (jam?.enabled && jam.history.length > 0)) && (
          <section className="conductor-timeline" aria-label="演奏安排">
            {jam?.enabled && jam.history.length > 0 && (
              <ol className="jam-history" aria-label="即兴演出段落">
                {[...jam.history, ...(jam.next && !jam.history.some(item => item.start_bar === jam.next?.start_bar) ? [jam.next] : [])]
                  .map(item => {
                    const current = jam.current?.start_bar === item.start_bar
                    const upcoming = state && item.start_bar > state.bar && !current
                    return (
                      <li key={item.start_bar} className={current ? 'current' : upcoming ? 'upcoming' : undefined}
                        aria-current={current ? 'step' : undefined}>
                        <span>{item.start_bar + 1}–{item.end_bar}</span>
                        <strong>{item.label}</strong>
                      </li>
                    )
                  })}
              </ol>
            )}
            {improvisation?.active && (
              <ChorusPreview chorus={improvisation.active} currentBar={state?.transport === 'stopped' ? undefined : state?.bar}
                transport={state?.transport} lineup={state?.settings.lineup} />
            )}
            {improvisation?.pending && <ChorusPreview chorus={improvisation.pending} lineup={state?.settings.lineup} />}
            {song && state && (
              <>
                <SongRoadmap song={song} state={state} startBar={songStart} pending={!!newSong} />
                <div className="timeline-actions">
                  {leaving !== null
                    ? <span>第 {leaving + 1} 小节回到八小节循环</span>
                    : <button type="button" disabled={disabled || requestInFlight} onClick={onLeaveSong}>退出歌曲</button>}
                </div>
              </>
            )}
          </section>
        )}
      </div>
    </section>
  )
}
