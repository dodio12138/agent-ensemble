// Original instrument definitions for the Agent Ensemble OSC output.
(
var directory = "ENSEMBLE_SYNTHDEF_DIR".getenv;
var levels = { |amp, gain, trim|
    amp.clip(0, 1).pow(1.35) * Lag.kr(gain.clip(0, 1), 0.012) * trim
};

if(directory.isNil, { "Set ENSEMBLE_SYNTHDEF_DIR before compiling.".error; 1.exit });

[
    SynthDef(\ae_drum_sample, { |out = 0, bufnum = 0, amp = 1, gain = 1, gate = 1,
        rate = 1, pan = 0, openness = 0|
        var signal = PlayBuf.ar(2, bufnum, BufRateScale.kr(bufnum) * rate,
            doneAction: Done.freeSelf);
        var envelope = EnvGen.kr(Env.asr(0.0006, 1, 0.028), gate, doneAction: Done.freeSelf);
        var color = Select.kr(openness, [amp.linlin(0, 1, 5000, 18000), 18000]);
        var audio = LPF.ar(signal, color) * envelope * gain.clip(0, 1);
        Out.ar(out, Balance2.ar(audio[0], audio[1], pan.clip(-1, 1)));
    }),

    SynthDef(\ae_kick, { |out = 0, freq = 65, amp = 1, gain = 1, gate = 1|
        var tuning = freq.clip(45, 120);
        var glide = EnvGen.ar(Env([tuning * 2.7, tuning * 0.86, tuning * 0.74],
            [0.022, 0.115], \exp));
        var body = SinOsc.ar(glide) + (SinOsc.ar(glide * 1.43) * 0.11);
        var beater = BPF.ar(WhiteNoise.ar, 2020, 0.48)
            * EnvGen.ar(Env.perc(0.0007, 0.018)) * 0.16;
        var envelope = EnvGen.ar(Env.perc(0.001, 0.28, curve: -6), doneAction: Done.freeSelf);
        Out.ar(out, Pan2.ar((body + beater) * envelope * levels.(amp, gain, 0.26)))
    }),

    SynthDef(\ae_snare, { |out = 0, freq = 73, amp = 1, gain = 1, gate = 1|
        var tuning = freq.clip(62, 105);
        var shell = (SinOsc.ar(tuning * 2.2) + (SinOsc.ar(tuning * 3.6) * 0.34))
            * EnvGen.ar(Env.perc(0.0008, 0.058));
        var wires = HPF.ar(WhiteNoise.ar, 850) + (BPF.ar(WhiteNoise.ar, 3100, 0.73) * 0.42);
        // The lowest snare note is a dry rim strike.
        var rim = (tuning < 72);
        var decay = Select.kr(rim, [0.164, 0.068]);
        var envelope = EnvGen.ar(Env.perc(0.0007, decay, curve: -5), doneAction: Done.freeSelf);
        var tone = (shell * (0.31 + (0.31 * rim))) + (wires * (0.69 - (0.38 * rim)));
        Out.ar(out, Pan2.ar(tone * envelope * levels.(amp, gain, 0.18), -0.08))
    }),

    SynthDef(\ae_hat, { |out = 0, freq = 92, amp = 1, gain = 1, gate = 1|
        var tuning = freq.clip(75, 200);
        var decay = tuning.linlin(82, 166, 0.045, 0.39).clip(0.045, 0.54);
        var strike = HPF.ar(WhiteNoise.ar, 4900);
        var metal = Mix(Ringz.ar(strike * 0.021,
            [4751, 6029, 7343, 8731] * tuning.linlin(75, 200, 0.91, 1.07),
            [0.014, 0.021, 0.025, 0.019]));
        var envelope = EnvGen.ar(Env.perc(0.001, decay, curve: -4), doneAction: Done.freeSelf);
        Out.ar(out, Pan2.ar((strike * 0.66 + metal * 0.21) * envelope * levels.(amp, gain, 0.25),
            0.17))
    }),

    SynthDef(\ae_tom, { |out = 0, freq = 110, amp = 1, gain = 1, gate = 1|
        var tuning = freq.clip(75, 190);
        var glide = EnvGen.ar(Env([tuning * 1.72, tuning, tuning * 0.94],
            [0.035, 0.19], \exp));
        var tone = SinOsc.ar(glide) + (SinOsc.ar(glide * 1.56) * 0.22);
        var membrane = BPF.ar(WhiteNoise.ar, 930, 0.62)
            * EnvGen.ar(Env.perc(0.001, 0.026)) * 0.21;
        var envelope = EnvGen.ar(Env.perc(0.001, 0.31, curve: -5), doneAction: Done.freeSelf);
        Out.ar(out, Pan2.ar((tone + membrane) * envelope * levels.(amp, gain, 0.185), -0.19))
    }),

    SynthDef(\ae_bass, { |out = 0, freq = 90, amp = 1, gain = 1, gate = 1|
        var tuning = freq.clip(16, 9000);
        var excited = LPF.ar(WhiteNoise.ar(0.8), 1650 + (amp.clip(0, 1) * 1100));
        var string = Pluck.ar(excited, Impulse.kr(0), 0.1, tuning.reciprocal, 1.55, 0.56);
        var body = SinOsc.ar(tuning)
            * EnvGen.kr(Env([0, 0.84, 0.65, 0.3], [0.008, 0.08, 1.27]));
        var envelope = EnvGen.kr(Env.adsr(0.007, 0.22, 0.53, 0.16), gate,
            doneAction: Done.freeSelf);
        var tone = (LPF.ar(string, 2600) * 0.76) + body;
        Out.ar(out, Pan2.ar(tone * envelope * levels.(amp, gain, 0.155), -0.06))
    }),

    SynthDef(\ae_electric_bass, { |out = 0, freq = 90, amp = 1, gain = 1, gate = 1|
        var tuning = freq.clip(25, 950);
        var pickup = LPF.ar(Pulse.ar(tuning, 0.32) * 0.18, 1250);
        var attack = LPF.ar(Pluck.ar(WhiteNoise.ar(0.5), Impulse.kr(0), 0.1,
            tuning.reciprocal, 1.7, 0.48), 3000);
        var envelope = EnvGen.kr(Env.adsr(0.009, 0.23, 0.53, 0.11), gate,
            doneAction: Done.freeSelf);
        var tone = (SinOsc.ar(tuning) * 0.72) + pickup + (attack * 0.95);
        Out.ar(out, Pan2.ar(tone * envelope * levels.(amp, gain, 0.152), -0.04))
    }),

    SynthDef(\ae_piano, { |out = 0, freq = 440, amp = 1, gain = 1, gate = 1|
        var tuning = freq.clip(8, 13000);
        var hammer = Impulse.kr(0);
        var partials = [1, 2.002, 3.012, 4.04, 6.09];
        var decay = [3.9, 1.51, 0.77, 0.38, 0.14] * (170 / tuning).pow(0.18).clip(0.55, 1.65);
        var weights = [0.75, 0.28, 0.175, 0.09, 0.034];
        var brightness = (0.54 + (amp.clip(0, 1) * 0.46));
        var tone = Mix(SinOsc.ar((tuning * partials).clip(8, 18000))
            * weights * Decay2.kr(hammer, 0.0025, decay) * [1, brightness, brightness, brightness, brightness]);
        var unison = SinOsc.ar(tuning * [0.9994, 1.0006]) * Decay2.kr(hammer, 0.003, decay[0]) * 0.15;
        var wood = BPF.ar(WhiteNoise.ar, 1850 + (tuning * 0.35), 0.55)
            * Decay2.kr(hammer, 0.0007, 0.021) * 0.075;
        var envelope = EnvGen.kr(Env.asr(0.004, 1, 0.22), gate, doneAction: Done.freeSelf);
        Out.ar(out, (Pan2.ar(tone + wood, 0.07) + unison) * envelope * levels.(amp, gain, 0.145))
    }),

    SynthDef(\ae_epiano, { |out = 0, freq = 440, amp = 1, gain = 1, gate = 1|
        var tuning = freq.clip(8, 13000);
        var strike = Impulse.kr(0);
        var modulation = Decay2.kr(strike, 0.003, 0.55)
            * (0.7 + (amp.clip(0, 1) * 2.5));
        var tines = SinOsc.ar(tuning * [0.9989, 1.0011],
            SinOsc.ar((tuning * [1.996, 2.004]).clip(8, 18100)) * modulation);
        var tone = tines * Decay2.kr(strike, 0.004, 2.9);
        var envelope = EnvGen.kr(Env.asr(0.005, 1, 0.32), gate, doneAction: Done.freeSelf);
        Out.ar(out, tone * envelope * levels.(amp, gain, 0.13))
    }),

    SynthDef(\ae_vibes, { |out = 0, freq = 440, amp = 1, gain = 1, gate = 1|
        var tuning = freq.clip(8, 13000);
        var weights = [0.83, 0.20, 0.11, 0.045];
        var tone = Mix(SinOsc.ar((tuning * [1, 3.99, 9.08, 14.3]).clip(8, 18000))
            * Decay2.kr(Impulse.kr(0), 0.002, [2.67, 0.9, 0.28, 0.11]) * weights);
        var tremolo = SinOsc.kr(4.4, [0, 0.8], 0.13, 0.87);
        var envelope = EnvGen.kr(Env.asr(0.003, 1, 0.35), gate, doneAction: Done.freeSelf);
        Out.ar(out, tone * tremolo * envelope * levels.(amp, gain, 0.126))
    }),

    SynthDef(\ae_organ, { |out = 0, freq = 440, amp = 1, gain = 1, gate = 1|
        var tuning = freq.clip(30, 3000);
        var pipes = Mix(SinOsc.ar(tuning * [0.5, 1, 2, 3, 4, 5, 6])
            * [0.21, 0.50, 0.29, 0.11, 0.07, 0.035, 0.02]);
        var envelope = EnvGen.kr(Env.asr(0.022, 1, 0.16), gate, doneAction: Done.freeSelf);
        Out.ar(out, Splay.ar(pipes + (DelayC.ar(pipes, 0.025, [0.013, 0.019]) * 0.19))
            * envelope * levels.(amp, gain, 0.097))
    }),

    SynthDef(\ae_clean_guitar, { |out = 0, freq = 330, amp = 1, gain = 1, gate = 1, art = 0|
        var tuning = freq.clip(95, 1900);
        var muted = art > 0.5;
        var strike = Impulse.kr(0);
        var pick = LPF.ar(WhiteNoise.ar, Select.kr(muted, [4300 + (amp * 2400), 1700]));
        var string = Pluck.ar(pick, strike, 0.025, tuning.reciprocal,
            Select.kr(muted, [2.2, 0.24]), Select.kr(muted, [0.51, 0.24]));
        var pickup = Mix(SinOsc.ar(tuning * [1, 2, 3, 4]) * [0.36, 0.21, 0.10, 0.035]
            * Decay2.kr(strike, 0.003, [1.42, 0.68, 0.31, 0.16]));
        var envelope = EnvGen.kr(Env.adsr(0.005, Select.kr(muted, [0.16, 0.056]),
            Select.kr(muted, [0.62, 0.20]), Select.kr(muted, [0.072, 0.035])), gate,
            doneAction: Done.freeSelf);
        var tone = LPF.ar(HPF.ar((string * 0.83) + (pickup * 1.35), 115), 3900);
        Out.ar(out, Pan2.ar(tone * envelope * levels.(amp, gain, 0.179), -0.31))
    }),

    SynthDef(\ae_lead_guitar, { |out = 0, freq = 440, amp = 1, gain = 1, gate = 1, art = 0|
        var tuning = freq.clip(130, 2100);
        var legato = art > 1.5;
        var harmonic = art > 2.5;
        var strike = Impulse.kr(0);
        var vibrato = SinOsc.kr(5.1, 0, 0.004) * Line.kr(0, 1, 0.32);
        var string = Pluck.ar(LPF.ar(WhiteNoise.ar, 5200), strike, 0.025,
            tuning.reciprocal, 1.6, 0.54);
        var driver = (Pulse.ar(tuning * (1 + vibrato), 0.38) * 0.24)
            + (SinOsc.ar(tuning) * 0.37) + (string * Select.kr(legato, [0.79, 0.20]));
        var driven = LPF.ar(HPF.ar((driver * (2.0 + (amp * 1.45))).tanh, 155), 3450);
        var overtone = Mix(SinOsc.ar(tuning * [1, 2, 3, 4]) * [0.33, 0.41, 0.08, 0.07]);
        var tone = Select.ar(harmonic, [driven, overtone * 0.64]);
        var envelope = EnvGen.kr(Env.adsr(Select.kr(legato, [0.008, 0.025]),
            0.21, Select.kr(harmonic, [0.55, 0.37]), Select.kr(harmonic, [0.12, 0.22])), gate,
            doneAction: Done.freeSelf);
        Out.ar(out, Pan2.ar(tone * envelope * levels.(amp, gain, 0.13), 0.29))
    }),

    SynthDef(\ae_strings, { |out = 0, freq = 330, amp = 1, gain = 1, gate = 1|
        var tuning = freq.clip(45, 2200);
        var player = [0.9974, 1.0001, 1.0028];
        var accents = [0, 0.78, 1.69];
        var attack = amp.linlin(0, 1, 0.21, 0.079);
        var vibrato = SinOsc.kr([4.7, 5.2, 5.6], accents, [0.0015, 0.0020, 0.0012])
            * Line.kr(0.18, 1, 0.34);
        var string = VarSaw.ar(tuning * player * (1 + vibrato), accents / (2 * pi), 0.24)
            * [0.26, 0.30, 0.25];
        var body = RLPF.ar(string, amp.linlin(0, 1, 1250, 3400), 0.57);
        var bow = BPF.ar(PinkNoise.ar(0.045), 1700, 0.34)
            * EnvGen.kr(Env.perc(0.014, 0.18));
        var envelope = EnvGen.kr(Env.adsr(attack, 0.25, 0.77, 0.28),
            gate, doneAction: Done.freeSelf);
        Out.ar(out, Splay.ar(HPF.ar(body + bow, 105), 0.60)
            * envelope * levels.(amp, gain, 0.135));
    }),

    SynthDef(\ae_pad, { |out = 0, freq = 330, amp = 1, gain = 1, gate = 1|
        var tuning = freq.clip(70, 3500);
        var drift = SinOsc.kr(0.39, [0, 1.57], 0.0019, 1);
        var body = VarSaw.ar(tuning * [0.997, 1.003] * drift, [0, 0.25], 0.64) * 0.35
            + (SinOsc.ar(tuning * [1.001, 0.999], [0, 0.4]) * 0.62);
        var cutoff = SinOsc.kr(0.63, [0, 1.4], 130, 980 + (amp * 280));
        var envelope = EnvGen.kr(Env.adsr(0.082, 0.42, 0.71, 0.27), gate,
            doneAction: Done.freeSelf);
        Out.ar(out, LPF.ar(HPF.ar(body, 100), cutoff) * envelope * levels.(amp, gain, 0.105))
    })
].do({ |voice| voice.writeDefFile(directory) });

"Agent Ensemble SynthDefs written.".postln;
0.exit;
)
