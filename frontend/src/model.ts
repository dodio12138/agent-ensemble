export const TICKS_PER_QUARTER = 960
export type TimeSignature = '4/4' | '3/4' | '6/8'
export type SectionRole = 'intro' | 'a' | 'b' | 'bridge' | 'outro'
export const SECTION_NAMES: Record<SectionRole, string> = {
  intro: '引子', a: 'A 段', b: 'B 段', bridge: '桥段', outro: '尾声',
}
export const METERS = {
  '4/4': { numerator: 4, denominator: 4, pulseTicks: 960, barTicks: 3840 },
  '3/4': { numerator: 3, denominator: 4, pulseTicks: 960, barTicks: 2880 },
  '6/8': { numerator: 6, denominator: 8, pulseTicks: 1440, barTicks: 2880 },
} as const

export function activeMeter(state?: State | null): TimeSignature {
  const meter = state?.meter
  if (meter) {
    if (meter.numerator === 3 && meter.denominator === 4) return '3/4'
    if (meter.numerator === 6 && meter.denominator === 8) return '6/8'
    if (meter.numerator === 4 && meter.denominator === 4) return '4/4'
  }
  return state?.settings.meter ?? '4/4'
}
export const CORE_TRACK_IDS = ['drums', 'bass', 'piano'] as const
const MODERN_TRACK_IDS = ['rhythm_guitar', 'lead_guitar', 'synth'] as const
const GAME_TRACK_IDS = ['strings', 'brass', 'melody', 'chip'] as const
export const TRACK_IDS = [...CORE_TRACK_IDS, ...MODERN_TRACK_IDS, ...GAME_TRACK_IDS, 'vocal'] as const
type CoreTrackId = typeof CORE_TRACK_IDS[number]
type AddedTrackId = typeof MODERN_TRACK_IDS[number] | typeof GAME_TRACK_IDS[number] | 'vocal'
export type TrackId = typeof TRACK_IDS[number]
export type Lineup = 'trio' | 'modern' | 'game'

export const LINEUPS = ['trio', 'modern', 'game'] as const
export const LINEUP_NAMES = { trio: '三重奏', modern: '现代乐队', game: '游戏配乐' } as const
export const LINEUP_SUBTITLES = {
  trio: '即兴三重奏 + 人声',
  modern: '现代即兴乐队 · 6 轨 + 人声',
  game: '游戏配乐编制 · 7 轨 + 人声',
} as const

const LINEUP_TRACKS: Record<Lineup, readonly TrackId[]> = {
  trio: [...CORE_TRACK_IDS, 'vocal'],
  modern: [...CORE_TRACK_IDS, ...MODERN_TRACK_IDS, 'vocal'],
  game: [...CORE_TRACK_IDS, ...GAME_TRACK_IDS, 'vocal'],
}

export function tracksFor(lineup?: Lineup): readonly TrackId[] {
  return LINEUP_TRACKS[lineup ?? 'trio']
}

/** The voice that takes melodic solos when the conductor hands the lead to the keys. */
export function soloistFor(lead: TrackId, lineup?: Lineup): TrackId {
  if (lead !== 'piano') return lead
  return lineup === 'modern' ? 'lead_guitar' : lineup === 'game' ? 'melody' : 'piano'
}

const pitchNames = ['C', 'C♯', 'D', 'D♯', 'E', 'F', 'F♯', 'G', 'G♯', 'A', 'A♯', 'B']

export function pitchName(pitch: number) {
  return pitchNames[pitch % 12] + (Math.floor(pitch / 12) - 1)
}

export const TRACK_INFO = {
  drums: { name: '鼓组', en: 'DRUMS', color: '#efaa75', range: [35, 59], program: 0 },
  bass: { name: '贝斯', en: 'BASS', color: '#78c9ad', range: [28, 55], program: 32 },
  piano: { name: '钢琴', en: 'PIANO', color: '#bcb0ec', range: [48, 88], program: 0 },
  rhythm_guitar: { name: '节奏吉他', en: 'RHYTHM GTR', color: '#ebcd80', range: [48, 76], program: 27 },
  lead_guitar: { name: '主奏吉他', en: 'LEAD GTR', color: '#e3a6bb', range: [55, 88], program: 30 },
  synth: { name: '合成器', en: 'SYNTH', color: '#94ccea', range: [55, 88], program: 89 },
  strings: { name: '弦乐', en: 'STRINGS', color: '#d9a3e6', range: [43, 84], program: 48 },
  brass: { name: '铜管', en: 'BRASS', color: '#e6c06e', range: [45, 79], program: 60 },
  melody: { name: '主旋律', en: 'THEME', color: '#8fd3c4', range: [60, 96], program: 73 },
  chip: { name: '芯片琶音', en: 'CHIP ARP', color: '#9fb6f0', range: [48, 96], program: 80 },
  vocal: { name: '人声', en: 'VOICE', color: '#f2a7a0', range: [48, 84], program: 53 },
} as const

export function trackLabel(track: TrackId, lineup?: Lineup) {
  const { name, en } = TRACK_INFO[track]
  return track === 'piano' && lineup && lineup !== 'trio' ? { name: '键盘', en: 'KEYS' } : { name, en }
}

export const STYLE_NAMES = {
  swing: '摇摆爵士',
  bossa: '波萨诺瓦',
  funk: '放克',
  ballad: '抒情',
} as const

export type Style = keyof typeof STYLE_NAMES
export const GENRE_NAMES = {
  ...STYLE_NAMES,
  rock: '摇滚',
  hard_rock: '硬摇滚',
  alternative_rock: '另类摇滚',
  pop: '流行',
  blues: '布鲁斯',
  reggae: '雷鬼',
  disco: '迪斯科',
  soul: '灵魂乐',
  cinematic: '电影配乐',
  ambient: '氛围音乐',
  electronic: '电子音乐',
  latin: '拉丁',
  country: '乡村',
  hip_hop: '嘻哈',
  classical: '古典',
  chiptune: '8-bit 芯片',
  platformer: '像素平台跳跃',
  rpg: 'RPG 冒险 · 村庄',
  battle: '史诗战斗 · Boss',
  exploration: '探索氛围',
  stealth: '悬疑潜行',
} as const

export const GROOVE_NAMES = {
  swing: '摇摆八分',
  bossa: '波萨诺瓦切分',
  straight_eighth: '平直八分',
  sixteenth: '十六分律动',
  shuffle: 'Shuffle（偏三连音）',
  one_drop: 'One Drop（第三拍重音）',
  four_on_floor: '四拍底鼓',
  half_time: '半拍感',
} as const

export type Genre = keyof typeof GENRE_NAMES
export type Groove = keyof typeof GROOVE_NAMES
export type CollaborationMode = 'solo' | 'shared' | 'negotiate'
export type AudioOutput = 'browser' | 'native' | 'supercollider'

export interface GrooveTransition {
  phase: 'pickup' | 'arrival'
  from_style: Style
  to_style: Style
  from_bpm: number
  to_bpm: number
  from_meter?: TimeSignature
  to_meter?: TimeSignature
  from_genre?: Genre | null
  to_genre?: Genre | null
  from_groove?: Groove | null
  to_groove?: Groove | null
}

export interface NegotiationFeedEvent {
  type: 'negotiation'
  sequence: number
  target_bar: number
  proposal_id: string
  stage: 'proposal' | 'accepted' | 'declined' | 'timeout'
  sender: TrackId
  recipient: TrackId
  kind: 'kick_pattern' | 'reserve_space' | 'lighter_drums'
  ticks: number[]
  time_signature?: TimeSignature
  detail: string
}

export interface MusicProfile {
  genre: Genre
  groove: Groove
  mode: 'major' | 'minor' | 'dorian' | 'mixolydian'
  harmony: 'keep' | 'pop_1564' | 'minor_1645' | 'mixolydian_174' | 'blues_8' | 'dorian_vamp' | 'minor_1767' | 'soul_251' | 'major_145'
  tonic: string
  reference?: string | null
  basis: 'genre' | 'reference' | 'inferred' | 'theory'
}

export interface ConductorPlan {
  style: Style
  intensity: number
  drums: 'balanced' | 'sparse' | 'driving'
  bass: 'walking' | 'root' | 'syncopated'
  piano: 'comp' | 'sustained' | 'syncopated'
  profile?: MusicProfile | null
  vocal?: VocalRole | null
}

export type VocalRole = 'off' | 'pad' | 'lead' | 'backing' | 'scat'
export const VOCAL_ROLE_NAMES: Record<VocalRole, string> = {
  off: '不唱', pad: '合唱铺底', lead: '无词领唱', backing: '句尾伴唱', scat: '爵士拟声',
}

export interface ConductorResult {
  source: 'llm' | 'rules' | 'preset'
  plan: ConductorPlan
  status: 'scheduled' | 'applied' | 'unchanged'
  effective_bar: number | null
  state_version: number | null
  reason: string | null
  model: string | null
  prompt_version: string
  attempts: number
  bpm?: number | null
  meter?: TimeSignature | null
  timing_boundary?: 'next_chorus' | null
  note?: string
  corrections?: string[]
}

export interface RefinementState {
  genre: string
  status: 'planning' | 'applied' | 'failed' | 'skipped' | 'superseded'
  reason?: string | null
  note?: string
  bpm?: number
  effective_bar?: number | null
  corrections?: string[]
}

export interface ImprovisationPhrase {
  lead: TrackId
  character: 'space' | 'answer' | 'build' | 'resolve'
}

export interface HarmonicMove {
  position: number
  technique: 'color' | 'inversion' | 'dominant' | 'borrowed' | 'deceptive' | 'tritone'
  chord: string
}

export interface ImprovisedChorus {
  start_bar: number
  end_bar: number
  source: 'llm' | 'rules'
  model?: string | null
  reason?: string | null
  plan: {
    progression: string[]
    phrases: ImprovisationPhrase[]
    harmonic_moves?: HarmonicMove[]
    relative_to_start?: boolean
  }
}

export interface ImprovisationState {
  auto: boolean
  planning: boolean
  active: ImprovisedChorus | null
  pending: ImprovisedChorus | null
  last_error: string | null
}

export interface SongSection {
  role: SectionRole
  bars: 4 | 8
  bpm: number
  meter: TimeSignature
  progression: string[]
  motif: 'rise' | 'fall' | 'arch' | 'repeat'
  texture: 'sparse' | 'full' | 'answer'
  keys_tone?: 'piano' | 'electric' | 'vibes' | 'organ'
  mix?: 'lead' | 'rhythm' | 'balanced'
  intensity: number
}

export interface Song {
  sections: SongSection[]
  tonic: string
  mode: MusicProfile['mode']
}

export interface SectionPosition {
  index: number
  role: SectionRole
  start_bar: number
  end_bar: number
  bar_in_section: number
  total_bars: number
}

export interface CompositionState {
  planning: boolean
  error: string | null
  source: 'llm' | 'rules' | null
  start_bar: number | null
}

export function songPosition(song: Song, bar: number, start: number, loop: boolean) {
  const total = song.sections.reduce((sum, section) => sum + section.bars, 0)
  const elapsed = bar - start
  if (elapsed < 0 || total < 1 || (!loop && elapsed >= total)) return null
  let offset = elapsed % total
  for (const section of song.sections) {
    if (offset < section.bars) return { section, offset }
    offset -= section.bars
  }
  return null
}

export interface Settings {
  bpm: number
  density: number
  progression: string[]
  loop: boolean
  seed: number
  conductor?: ConductorPlan
  collaboration?: CollaborationMode
  lineup?: Lineup
  meter?: TimeSignature
  song?: Song | null
  glide?: Partial<Record<GlideTrack, Glide>>
}

export interface Glide {
  on: boolean
  amount: number
}

// Melodic tracks that can slide, tie and run between notes, with what that sounds like.
export const GLIDES = {
  vocal: { button: '转音', title: '人声：滑音、粘连和长音前的转音' },
  lead_guitar: { button: '推弦', title: '主奏吉他：推弦、滑弦和击弦连奏' },
  melody: { button: '滑音', title: '主旋律：滑音、连奏和装饰音' },
  chip: { button: '滑音', title: '芯片琶音：方波滑音' },
  bass: { button: '滑音', title: '贝斯：滑进音和换把滑音（不改变律动时值）' },
  strings: { button: '滑音', title: '弦乐：单音线条的换把滑音和连弓，和弦不受影响' },
  brass: { button: '滑音', title: '铜管：唇滑和起音上滑' },
} as const
export type GlideTrack = keyof typeof GLIDES
export const DEFAULT_GLIDE: Record<GlideTrack, Glide> = {
  vocal: { on: true, amount: 0.5 },
  lead_guitar: { on: false, amount: 0.5 },
  melody: { on: false, amount: 0.5 },
  chip: { on: false, amount: 0.5 },
  bass: { on: false, amount: 0.5 },
  strings: { on: false, amount: 0.5 },
  brass: { on: false, amount: 0.5 },
}

export interface TrackSettings {
  volume: number
  muted: boolean
  program: number
}

type TrackSettingsMap = Record<CoreTrackId, TrackSettings> & Partial<Record<AddedTrackId, TrackSettings>>

export interface PlayerPhrase {
  register: 'low' | 'middle' | 'high'
  activity: 'sparse' | 'busy'
  accent_tick: number | null
  velocity: 'soft' | 'strong'
}

export interface PlayerResponse {
  phrase: PlayerPhrase
  drums: string
  bass: string
  piano: string
}

export interface MidiState {
  ports: { id: string; name: string }[]
  selected: string | null
  connection: 'disabled' | 'waiting' | 'connected' | 'unavailable'
  error: string | null
  volume: number
  program: number
  adaptive: boolean
  pressed: number[]
  sounding: number[]
}

export interface MidiPatch {
  port?: string | null
  volume?: number
  program?: number
  adaptive?: boolean
}

export interface Note {
  event_id: string
  track_id: TrackId
  bar: number
  start_tick: number
  duration_ticks: number
  pitch: number
  velocity: number
  channel: number
}

export interface Plan {
  bar: number
  notes: Note[]
  fallback_tracks: TrackId[]
  version: number
  intents?: { track_id: TrackId; intent: string; status?: string }[]
  transition?: GrooveTransition | null
  player_response?: PlayerResponse | null
  harmony?: string | null
  time_signature?: TimeSignature
  bpm?: number
  improvisation_phrase?: ImprovisationPhrase | null
}

export interface Pending {
  bar: number
  state_version?: number
  settings: Settings
  song_origin?: number
  tracks: TrackSettingsMap
  transition?: GrooveTransition | null
  pickup_bar?: number | null
}

export interface State {
  id: string
  transport: 'stopped' | 'playing' | 'paused'
  bar: number
  tick: number
  bpm?: number
  meter?: { numerator: number; denominator: number }
  bar_ticks?: number
  pulse_ticks?: number
  ticks_per_quarter?: number
  settings: Settings
  song_origin?: number
  tracks: TrackSettingsMap
  pending: Pending | null
  pending_changes?: Pending[]
  plans: Plan[]
  sequence: number
  audio_output?: AudioOutput
  audio_preferred?: AudioOutput
  audio_fallback?: AudioOutput
  audio_reason?: string | null
  audio_device?: 'logic' | 'fluidsynth' | null
  audio_routes?: AudioOutput[]
  vocal_role?: VocalRole
  audio_active?: BandEvent[]
  locked_until_bar?: number
  state_version?: number
  conductor?: ConductorResult | null
  conductor_model?: string | null
  improvisation?: ImprovisationState
  composition?: CompositionState
  section?: SectionPosition | null
  negotiation?: NegotiationFeedEvent[]
  transition?: GrooveTransition | null
  midi?: MidiState
  link?: LinkState
  jam?: JamState
  refinement?: RefinementState | null
  timing?: TimingState
  remote_agents?: Partial<Record<TrackId, RemoteAgentState>>
}

export interface LinkState {
  available: boolean
  enabled: boolean
  start_stop: boolean
  error: string | null
  peers?: number
  tempo?: number
  beat?: number
  phase?: number
  quantum?: number
  playing?: boolean
  corrections?: number
  last_shift_ms?: number
}

export interface JamSection {
  start_bar: number
  end_bar: number
  move: string
  label: string
}

export interface JamState {
  enabled: boolean
  profile: string
  profiles: Record<string, string>
  current: JamSection | null
  next: JamSection | null
  history: JamSection[]
}

export interface JamPatch {
  enabled?: boolean
  profile?: string
}

export interface LinkPatch {
  enabled?: boolean
  start_stop?: boolean
}

export interface TimingState {
  engine: 'native' | 'python'
  promoted: boolean
  waiting_for_downbeat: boolean
  samples: number
  p50_us: number | null
  p99_us: number | null
  max_us: number | null
}

export interface RemoteAgentState {
  name: string
  connected_at: number
  deadline_ms: number
  last_intent: string | null
  requests: number
  answered: number
  late: number
  invalid: number
  latency_ms: number | null
}

export interface BandEvent {
  articulation?: 'normal' | 'palm_mute' | 'legato' | 'harmonic'
  slide_from?: number
  slide_ms?: number
  sequence: number
  type: string
  track_id?: TrackId
  event_id?: string
  pitch?: number
  velocity?: number
  volume?: number
  program?: number
  bar?: number
  audio_output?: AudioOutput
  resume?: BandEvent[]
}

export interface Frame {
  state: State
  events: BandEvent[]
  reset?: boolean
}

export interface SoundNote {
  expiry: number
  trackId: TrackId
}
