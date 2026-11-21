import type { BandEvent, TrackId } from './model'

type VoiceTrack = TrackId | 'player'

const modernTone = {
  rhythm_guitar: {
    pan: -0.32, cutoff: 3350, highpass: 110, attack: 0.006, decay: 0.25, sustain: 0.25, level: 0.20,
    partials: [
      { ratio: 1, gain: 0.69, wave: 'triangle' }, { ratio: 2, gain: 0.19, wave: 'sine' },
      { ratio: 3, gain: 0.13, wave: 'sine' }, { ratio: 4, gain: 0.06, wave: 'sine' },
    ],
  },
  lead_guitar: {
    pan: 0.29, cutoff: 2680, highpass: 175, attack: 0.009, decay: 0.30, sustain: 0.42, level: 0.16,
    partials: [
      { ratio: 1, gain: 0.57, wave: 'sawtooth' }, { ratio: 1.002, gain: 0.20, wave: 'square' },
      { ratio: 2, gain: 0.23, wave: 'sine' },
    ],
  },
  synth: {
    pan: 0.04, cutoff: 1070, highpass: 95, attack: 0.08, decay: 0.50, sustain: 0.68, level: 0.15,
    partials: [
      { ratio: 0.997, gain: 0.29, wave: 'sawtooth' }, { ratio: 1.003, gain: 0.24, wave: 'triangle' },
      { ratio: 1, gain: 0.56, wave: 'sine' },
    ],
  },
  strings: {
    pan: -0.06, cutoff: 2080, highpass: 105, attack: 0.14, decay: 0.33, sustain: 0.74, level: 0.14,
    partials: [
      { ratio: 0.9974, gain: 0.26, wave: 'sawtooth' }, { ratio: 1.0001, gain: 0.31, wave: 'sawtooth' },
      { ratio: 1.0028, gain: 0.25, wave: 'triangle' },
    ],
  },
  // Game-score voices, chosen by General MIDI program.
  square: {
    pan: 0.1, cutoff: 5200, highpass: 120, attack: 0.003, decay: 0.08, sustain: 0.78, level: 0.075,
    partials: [{ ratio: 1, gain: 0.8, wave: 'square' }],
  },
  saw: {
    pan: -0.1, cutoff: 3600, highpass: 120, attack: 0.004, decay: 0.12, sustain: 0.7, level: 0.075,
    partials: [{ ratio: 1, gain: 0.8, wave: 'sawtooth' }],
  },
  brass: {
    pan: 0.18, cutoff: 1650, highpass: 90, attack: 0.045, decay: 0.28, sustain: 0.72, level: 0.13,
    partials: [
      { ratio: 1, gain: 0.55, wave: 'sawtooth' }, { ratio: 1.004, gain: 0.25, wave: 'sawtooth' },
      { ratio: 2, gain: 0.12, wave: 'triangle' },
    ],
  },
  flute: {
    pan: -0.12, cutoff: 3900, highpass: 220, attack: 0.06, decay: 0.2, sustain: 0.8, level: 0.2,
    partials: [
      { ratio: 1, gain: 0.82, wave: 'sine' }, { ratio: 2, gain: 0.16, wave: 'triangle' },
      { ratio: 3, gain: 0.05, wave: 'sine' },
    ],
  },
  pluck: {
    pan: -0.22, cutoff: 3200, highpass: 90, attack: 0.003, decay: 0.55, sustain: 0.04, level: 0.24,
    partials: [
      { ratio: 1, gain: 0.7, wave: 'triangle' }, { ratio: 2, gain: 0.22, wave: 'sine' },
      { ratio: 3, gain: 0.08, wave: 'sine' },
    ],
  },
  bell: {
    pan: 0.26, cutoff: 6200, highpass: 300, attack: 0.002, decay: 0.9, sustain: 0.03, level: 0.15,
    partials: [
      { ratio: 1, gain: 0.66, wave: 'sine' }, { ratio: 2.76, gain: 0.2, wave: 'sine' },
      { ratio: 5.4, gain: 0.08, wave: 'sine' },
    ],
  },
} as const satisfies Record<string, {
  pan: number; cutoff: number; highpass: number; attack: number; decay: number; sustain: number; level: number
  partials: ReadonlyArray<{ ratio: number; gain: number; wave: OscillatorType }>
}>

type ToneName = keyof typeof modernTone
const GAME_TRACKS: Partial<Record<VoiceTrack, ToneName>> = {
  strings: 'strings', brass: 'brass', melody: 'flute', chip: 'bell',
}

/** Pick an oscillator voice for a program; null keeps the original piano and bass synths. */
function toneFor(track: VoiceTrack, program?: number): ToneName | null {
  if (track === 'rhythm_guitar' || track === 'lead_guitar') return track
  if (track === 'synth') return program === 48 ? 'strings' : 'synth'
  if (track === 'drums' || track === 'bass' || track === 'player' || program === undefined) return GAME_TRACKS[track] ?? null
  if (program === 80) return 'square'
  if (program === 81) return 'saw'
  if (program === 45 || program === 46 || program === 24 || program === 25) return 'pluck'
  if (program >= 40 && program <= 51) return 'strings'
  if (program >= 56 && program <= 63) return 'brass'
  if (program >= 64 && program <= 79) return 'flute'
  if (program >= 8 && program <= 14 && track !== 'piano') return 'bell'
  return GAME_TRACKS[track] ?? null
}

// Vowel formants (Hz, gain): "ah" for a choir, "oo" for the sung line, a brighter synth voice.
const VOWELS = {
  ah: [[800, 1], [1150, 0.5], [2900, 0.22]],
  oo: [[350, 1], [620, 0.42], [2400, 0.12]],
  synth: [[650, 1], [1080, 0.6], [2600, 0.3]],
} as const

interface Slide {
  ratio: number // start frequency as a ratio of the target
  seconds: number
}

/** A note that slides in: start at the earlier pitch and glide to the written one. */
function slideOf(event: BandEvent): Slide | undefined {
  if (event.slide_from === undefined || event.pitch === undefined) return undefined
  return { ratio: 2 ** ((event.slide_from - event.pitch) / 12), seconds: Math.max(0.015, (event.slide_ms ?? 80) / 1000) }
}

function glideTo(frequency: AudioParam, target: number, now: number, slide?: Slide) {
  frequency.setValueAtTime(target * (slide?.ratio ?? 1), now)
  if (slide) frequency.exponentialRampToValueAtTime(target, now + slide.seconds)
}

interface Voice {
  track: VoiceTrack
  output: GainNode
  sources: AudioScheduledSourceNode[]
  drumPitch?: number
}

export class StudioSynth {
  private context: AudioContext | null = null
  private master: GainNode | null = null
  private playerOutput: GainNode | null = null
  private playerVolume = 0.8
  private noise: AudioBuffer | null = null
  private drumSamples = new Map<string, AudioBuffer>()
  private drumLoads = new Set<string>()
  private voices = new Map<string, Voice>()
  private enabled = true

  async unlock() {
    if (!this.context) {
      const context = new AudioContext({ latencyHint: 'interactive' })
      const master = context.createGain()
      const limiter = context.createDynamicsCompressor()
      master.gain.value = 0.63
      limiter.threshold.value = -12
      limiter.ratio.value = 6
      master.connect(limiter).connect(context.destination)
      const playerOutput = context.createGain()
      playerOutput.gain.value = this.playerVolume
      playerOutput.connect(master)
      const noise = context.createBuffer(1, context.sampleRate / 2, context.sampleRate)
      const samples = noise.getChannelData(0)
      for (let index = 0; index < samples.length; index++) samples[index] = Math.random() * 2 - 1
      this.context = context
      this.master = master
      this.playerOutput = playerOutput
      this.noise = noise
      for (const pitch of [36, 38, 42, 46, 49, 51]) {
        for (const layer of [0, 1]) this.loadDrum(context, pitch, layer)
      }
    }
    if (this.context.state !== 'running') await this.context.resume()
  }

  toggle(enabled: boolean) {
    this.enabled = enabled
    if (!enabled) this.silence()
  }

  setPlayerVolume(volume: number) {
    if (volume === this.playerVolume) return
    this.playerVolume = volume
    if (this.context && this.playerOutput) {
      this.playerOutput.gain.setTargetAtTime(volume, this.context.currentTime, 0.012)
    }
  }

  event(event: BandEvent) {
    if (event.type === 'silence') {
      this.silenceBand(event.track_id)
      return
    }
    if (event.type === 'midi_silence') {
      this.silence('player')
      return
    }
    if ((event.type === 'note_off' || event.type === 'midi_off') && event.event_id) {
      this.release(event.event_id)
      return
    }
    const player = event.type === 'midi_on'
    const track = player ? 'player' : event.track_id
    const context = this.context
    const bus = player ? this.playerOutput : this.master
    if (!this.enabled || context?.state !== 'running' || !bus) return
    if ((!player && event.type !== 'note_on') || !track || !event.event_id || event.pitch === undefined) return
    this.release(event.event_id)
    const volume = (player ? 1 : event.volume ?? 0.8) * (event.velocity ?? 85) / 127
    if (volume === 0) return

    const output = context.createGain()
    output.connect(bus)
    const voice: Voice = { track, output, sources: [] }
    this.voices.set(event.event_id, voice)
    let tone: ToneName | null = null
    if (track === 'vocal') {
      this.sing(context, event, volume, voice)
    } else if (track === 'drums') {
      this.drum(
        context, event.pitch, volume, voice, event.event_id,
        event.velocity ?? 85, event.volume ?? 0.8,
      )
    } else if (track !== 'player' && (tone = toneFor(track, event.program))) {
      this.modernInstrument(context, event.pitch, volume, voice, tone, event.articulation, slideOf(event))
    } else {
      this.instrument(context, event.pitch, volume, voice, event.program ?? 0, slideOf(event))
    }
  }

  resume(notes: BandEvent[]) {
    for (const note of notes) {
      if (note.event_id && !this.voices.has(note.event_id)) this.event(note)
    }
  }

  private loadDrum(context: AudioContext, pitch: number, layer: number) {
    const key = `${pitch}-${layer}`
    if (this.drumLoads.has(key)) return
    this.drumLoads.add(key)
    void fetch(`/drum-kit/${pitch}/${layer}`)
      .then(result => {
        if (!result.ok) return null
        return result.arrayBuffer().then(bytes => context.decodeAudioData(bytes))
      })
      .then(sample => { if (sample) this.drumSamples.set(key, sample) })
      .catch(() => { /* Synthesized drums remain available. */ })
  }

  private drum(
    context: AudioContext, pitch: number, volume: number, voice: Voice, id: string,
    velocity: number, trackVolume: number,
  ) {
    const now = context.currentTime
    const { output, sources } = voice
    voice.drumPitch = pitch
    if (pitch === 42 || pitch === 44) {
      for (const [other, ringing] of this.voices) {
        if (ringing.drumPitch === 46) this.release(other, true)
      }
    }
    const layer = velocity >= 83 ? 1 : 0
    const sample = this.drumSamples.get(`${pitch}-${layer}`)
    if (sample) {
      const shot = context.createBufferSource()
      shot.buffer = sample
      output.gain.value = trackVolume * Math.min(1.25, (velocity / (layer ? 104 : 60)) ** 0.7)
      shot.connect(output)
      sources.push(shot)
      shot.onended = () => {
        output.disconnect()
        if (this.voices.get(id) === voice) this.voices.delete(id)
      }
      shot.start(now)
      return
    }
    this.loadDrum(context, pitch, layer)
    let duration: number
    if (pitch === 35 || pitch === 36 || (pitch >= 41 && pitch <= 45 && pitch !== 42 && pitch !== 44)) {
      const oscillator = context.createOscillator()
      const kick = pitch <= 36
      duration = kick ? 0.24 : 0.18
      oscillator.frequency.setValueAtTime(kick ? 125 : 150 + (pitch - 41) * 25, now)
      oscillator.frequency.exponentialRampToValueAtTime(kick ? 45 : 75, now + duration * 0.75)
      oscillator.connect(output)
      output.gain.value = volume * (kick ? 0.64 : 0.27)
      sources.push(oscillator)
    } else {
      const source = context.createBufferSource()
      source.buffer = this.noise
      const filter = context.createBiquadFilter()
      const snare = pitch === 37 || pitch === 38 || pitch === 40
      const ride = pitch === 49 || pitch >= 51
      duration = snare ? 0.16 : ride ? 0.32 : 0.083
      filter.type = snare ? 'bandpass' : 'highpass'
      filter.frequency.value = snare ? 1700 : 6800
      filter.Q.value = snare ? 0.65 : 0.8
      source.connect(filter).connect(output)
      output.gain.value = volume * (snare ? 0.52 : 0.23)
      sources.push(source)
    }
    output.gain.exponentialRampToValueAtTime(0.0001, now + duration)
    const source = sources[0]
    source.onended = () => {
      output.disconnect()
      if (this.voices.get(id) === voice) this.voices.delete(id)
    }
    source.start(now)
    source.stop(now + duration + 0.015)
  }

  private modernInstrument(
    context: AudioContext, pitch: number, volume: number, voice: Voice, track: keyof typeof modernTone,
    articulation: BandEvent['articulation'], slide?: Slide,
  ) {
    const now = context.currentTime
    const frequency = 440 * 2 ** ((pitch - 69) / 12)
    const tone = modernTone[track]
    const highpass = context.createBiquadFilter()
    highpass.type = 'highpass'
    highpass.frequency.value = tone.highpass
    const lowpass = context.createBiquadFilter()
    lowpass.type = 'lowpass'
    const muted = articulation === 'palm_mute'
    const harmonic = articulation === 'harmonic'
    const legato = articulation === 'legato'
    lowpass.frequency.value = (muted ? 1400 : tone.cutoff) + volume * 430
    const pan = context.createStereoPanner()
    pan.pan.value = tone.pan
    if (track === 'lead_guitar' && !harmonic) {
      const distortion = context.createWaveShaper()
      distortion.curve = Float32Array.from({ length: 256 }, (_, index) => Math.tanh(3.0 * (index / 127.5 - 1)))
      distortion.oversample = '2x'
      highpass.connect(distortion).connect(lowpass)
    } else {
      highpass.connect(lowpass)
    }
    lowpass.connect(pan).connect(voice.output)
    const partials = harmonic
      ? [{ ratio: 1, gain: 0.34, wave: 'sine' as const }, { ratio: 2, gain: 0.5, wave: 'sine' as const },
        { ratio: 4, gain: 0.09, wave: 'sine' as const }]
      : tone.partials
    for (const [index, partial] of partials.entries()) {
      const oscillator = context.createOscillator()
      const partialGain = context.createGain()
      oscillator.type = partial.wave
      glideTo(oscillator.frequency, frequency * partial.ratio, now, slide)
      partialGain.gain.value = partial.gain
      oscillator.connect(partialGain).connect(highpass)
      oscillator.start(now)
      voice.sources.push(oscillator)
      if (track === 'strings' || track === 'flute') {
        const vibrato = context.createOscillator()
        const depth = context.createGain()
        vibrato.frequency.value = 4.7 + index * 0.4
        depth.gain.setValueAtTime(0.15, now)
        depth.gain.linearRampToValueAtTime(2.6 + index * 0.6, now + 0.33)
        vibrato.connect(depth).connect(oscillator.detune)
        vibrato.start(now)
        voice.sources.push(vibrato)
      }
    }
    const peak = Math.max(0.0002, volume * tone.level)
    const gain = voice.output.gain
    gain.setValueAtTime(0.0001, now)
    const attack = legato || slide ? 0.026 : tone.attack
    gain.exponentialRampToValueAtTime(peak, now + attack)
    gain.exponentialRampToValueAtTime(Math.max(0.00011, peak * (muted ? 0.16 : tone.sustain)),
      now + attack + (muted ? 0.075 : tone.decay))
  }

  private instrument(
    context: AudioContext, pitch: number, volume: number, voice: Voice, program: number, slide?: Slide,
  ) {
    const now = context.currentTime
    const frequency = 440 * 2 ** ((pitch - 69) / 12)
    const bass = voice.track === 'bass'
    const electricBass = bass && program >= 33 && program <= 39
    const organ = !bass && program >= 16 && program <= 20
    const brightPiano = voice.track === 'player' && program === 1
    const vibes = voice.track === 'player' && program === 11
    const group = Math.floor(program / 8)
    const filter = context.createBiquadFilter()
    filter.type = 'lowpass'
    filter.frequency.value = bass
      ? (electricBass ? 1100 : 540 + group * 40)
      : (brightPiano ? 3400 : 2350 + group * 135)
    filter.connect(voice.output)

    const partials: { ratio: number; gain: number; wave: OscillatorType }[] = organ
      ? [{ ratio: 0.5, gain: 0.21, wave: 'sine' }, { ratio: 1, gain: 0.50, wave: 'sine' },
         { ratio: 2, gain: 0.29, wave: 'sine' }, { ratio: 3, gain: 0.11, wave: 'sine' },
         { ratio: 4, gain: 0.07, wave: 'sine' }]
      : electricBass
      ? [{ ratio: 1, gain: 0.83, wave: 'triangle' }, { ratio: 2, gain: 0.27, wave: 'triangle' },
         { ratio: 3, gain: 0.14, wave: 'sine' }]
      : bass
      ? [{ ratio: 1, gain: 1, wave: 'sine' },
         { ratio: 2, gain: 0.26 + group * 0.015, wave: 'triangle' }]
      : [{ ratio: 1, gain: 1, wave: group > 3 ? 'triangle' : 'sine' },
         { ratio: 2, gain: brightPiano ? 0.55 : program === 4 || vibes ? 0.16 : 0.37, wave: 'sine' },
         { ratio: vibes ? 4.02 : program === 4 ? 5.01 : 3.005, gain: vibes ? 0.18 : 0.10, wave: 'sine' }]
    for (const partial of partials) {
      const carrier = context.createOscillator()
      const mix = context.createGain()
      carrier.type = partial.wave
      glideTo(carrier.frequency, frequency * partial.ratio, now, slide)
      mix.gain.value = partial.gain
      carrier.connect(mix).connect(filter)
      carrier.start(now)
      voice.sources.push(carrier)
    }
    const peak = Math.max(0.0002, volume * (bass ? 0.40 : 0.29))
    const gain = voice.output.gain
    gain.setValueAtTime(0.0001, now)
    if (organ) {
      gain.exponentialRampToValueAtTime(peak * 0.72, now + 0.03)
      return
    }
    gain.exponentialRampToValueAtTime(peak, now + (bass ? 0.013 : 0.005))
    const sustain = electricBass ? 0.51 : bass ? 0.32 : 0.15
    gain.exponentialRampToValueAtTime(Math.max(0.00011, peak * sustain), now + (bass ? 0.21 : 0.40))
    gain.exponentialRampToValueAtTime(0.0001, now + (voice.track === 'player' ? 12 : 4))
  }

  /** A wordless voice: a buzzing source through vowel formants, sliding between notes. */
  private sing(context: AudioContext, event: BandEvent, volume: number, voice: Voice) {
    const now = context.currentTime
    const pitch = event.pitch ?? 60
    const target = 440 * 2 ** ((pitch - 69) / 12)
    const choir = event.program === 52
    const vowel = choir ? VOWELS.ah : event.program === 54 ? VOWELS.synth : VOWELS.oo
    const slide = event.slide_from !== undefined
    const start = slide ? 440 * 2 ** ((event.slide_from! - 69) / 12) : target
    const glide = Math.max(0.02, (event.slide_ms ?? 80) / 1000)

    const mix = context.createGain()
    const lowpass = context.createBiquadFilter()
    lowpass.type = 'lowpass'
    lowpass.frequency.value = choir ? 3800 : 4600
    const pan = context.createStereoPanner()
    pan.pan.value = choir ? 0 : -0.04
    mix.connect(lowpass).connect(pan).connect(voice.output)
    const bands = vowel.map(([frequency, gain]) => {
      const band = context.createBiquadFilter()
      band.type = 'bandpass'
      band.frequency.value = frequency
      band.Q.value = frequency < 1000 ? 6 : 9
      const level = context.createGain()
      level.gain.value = gain * 2.4
      band.connect(level).connect(mix)
      return band
    })
    // A choir is several singers slightly apart; a soloist is one voice.
    const detunes = choir ? [-9, 0, 8] : [0]
    const vibrato = context.createOscillator()
    vibrato.frequency.value = 5.2
    const depth = context.createGain()
    depth.gain.setValueAtTime(0, now)
    // Singers start a note straight and let the vibrato bloom as it is held.
    depth.gain.setValueAtTime(0, now + glide + 0.12)
    depth.gain.linearRampToValueAtTime(choir ? 9 : 16, now + glide + 0.5)
    vibrato.connect(depth)
    vibrato.start(now)
    voice.sources.push(vibrato)
    for (const cents of detunes) {
      const source = context.createOscillator()
      source.type = 'sawtooth'
      source.detune.value = cents
      source.frequency.setValueAtTime(start, now)
      if (slide) source.frequency.exponentialRampToValueAtTime(target, now + glide)
      depth.connect(source.detune)
      for (const band of bands) source.connect(band)
      source.start(now)
      voice.sources.push(source)
    }
    const peak = Math.max(0.0002, volume * (choir ? 0.09 : 0.13))
    const gain = voice.output.gain
    gain.setValueAtTime(0.0001, now)
    // A tied note keeps the breath going, so it barely re-attacks.
    gain.exponentialRampToValueAtTime(peak * (slide ? 0.9 : 1), now + (slide ? 0.025 : choir ? 0.18 : 0.06))
    gain.exponentialRampToValueAtTime(peak * 0.85, now + 0.4)
  }

  private release(id: string, immediately = false) {
    const voice = this.voices.get(id)
    const context = this.context
    if (!voice || !context) return
    if (!immediately && voice.track === 'drums') return
    this.voices.delete(id)
    const now = context.currentTime
    const end = now + (immediately ? 0.02 : voice.track === 'synth' ? 0.23 : 0.09)
    const gain = voice.output.gain
    gain.cancelScheduledValues(now)
    gain.setTargetAtTime(0.0001, now, immediately ? 0.004 : voice.track === 'synth' ? 0.05 : 0.02)
    for (const source of voice.sources) source.stop(end)
    voice.sources[0].onended = () => voice.output.disconnect()
  }

  silenceBand(track?: TrackId) {
    for (const [id, voice] of this.voices) {
      if (voice.track !== 'player' && (!track || voice.track === track)) this.release(id, true)
    }
  }

  silence(track?: VoiceTrack) {
    for (const [id, voice] of this.voices) {
      if (!track || voice.track === track) this.release(id, true)
    }
  }

  close() {
    this.silence()
    void this.context?.close()
  }
}
