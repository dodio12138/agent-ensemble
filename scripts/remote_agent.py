"""A sample remote musician: run it in another process to take over one track.

    .venv/bin/python -m scripts.remote_agent --track bass
    .venv/bin/python -m scripts.remote_agent --track piano --server http://127.0.0.1:8765

It joins the session open in the console (else the newest) unless --session is given. Any program that speaks the
same JSON messages over a WebSocket can play instead, in any language:

    -> {"type": "hello", "name": "...", "deadline_ms": 300}
    <- {"type": "welcome", ...}
    <- {"type": "plan", "request_id": "...", "context": {...}}
    -> {"type": "notes", "request_id": "...", "intent": "...",
        "notes": [{"start_tick": 0, "duration_ticks": 900, "pitch": 38, "velocity": 90}]}

Ticks are relative to the bar at ``context["ppq"]`` per quarter note. A reply that
misses the deadline, or a note outside ``context["range"]``, falls back to the
built-in musician for that bar.
"""

import argparse
import asyncio
import json
import random
import urllib.request

import websockets


def arpeggio(context: dict) -> tuple[str, list[dict]]:
    """Walk chord tones on each pulse, inside the track's range."""
    rng = random.Random(f"{context['seed']}:{context['bar']}:remote")
    chord = context["chord"]
    low, high = context["range"]["low"], context["range"]["high"]
    tones = sorted({(chord["root"] + interval) % 12 for interval in chord["intervals"]})
    base = low + (chord["bass"] - low) % 12
    pulse = context["pulse_ticks"]
    pulses = context["bar_ticks"] // pulse
    density = context["density"]
    notes = []
    for index in range(pulses):
        if index and rng.random() > 0.35 + density:
            continue
        pitch_class = chord["bass"] if index == 0 else rng.choice(tones)
        pitch = base + (pitch_class - base) % 12 + (12 if index % 2 and density > 0.6 else 0)
        while pitch > high:
            pitch -= 12
        notes.append(
            {
                "start_tick": index * pulse,
                "duration_ticks": pulse - pulse // 8,
                "pitch": max(low, pitch),
                "velocity": 100 if index == 0 else 72 + rng.randrange(16),
            }
        )
    if context["track"] == "drums":
        # Drums use General MIDI percussion: kick on the downbeats, hats on every pulse.
        notes = [
            {
                "start_tick": tick,
                "duration_ticks": 120,
                "pitch": 36 if i % 2 == 0 else 42,
                "velocity": 96 if i % 2 == 0 else 64,
            }
            for i, tick in enumerate(range(0, context["bar_ticks"], pulse))
        ]
    return f"远程琶音 · {chord['symbol']}", notes


def newest_session(server: str) -> str:
    with urllib.request.urlopen(f"{server}/sessions", timeout=5) as response:
        sessions = json.load(response)
    if not sessions:
        raise SystemExit("No session yet: open the console first.")
    return sessions[0]["id"]


async def play(server: str, session: str, track: str, name: str, deadline: int) -> None:
    url = f"{server.replace('http', 'ws', 1)}/sessions/{session}/agents/{track}"
    async with websockets.connect(url) as socket:
        await socket.send(json.dumps({"type": "hello", "name": name, "deadline_ms": deadline}))
        welcome = json.loads(await socket.recv())
        if welcome.get("type") != "welcome":
            raise SystemExit(welcome.get("message", "Connection refused"))
        print(f"Playing {track} in session {session}. Ctrl-C to hand it back.", flush=True)
        async for raw in socket:
            message = json.loads(raw)
            if message.get("type") != "plan":
                continue
            intent, notes = arpeggio(message["context"])
            await socket.send(
                json.dumps(
                    {
                        "type": "notes",
                        "request_id": message["request_id"],
                        "intent": intent,
                        "notes": notes,
                    },
                    ensure_ascii=False,
                )
            )


def main() -> None:
    parser = argparse.ArgumentParser(description="Play one track from a separate process")
    parser.add_argument("--server", default="http://127.0.0.1:8765")
    parser.add_argument("--session", help="Session ID; defaults to the newest session")
    parser.add_argument("--track", default="bass")
    parser.add_argument("--name", default="远程乐手")
    parser.add_argument("--deadline-ms", type=int, default=300)
    args = parser.parse_args()
    session = args.session or newest_session(args.server)
    try:
        asyncio.run(play(args.server, session, args.track, args.name, args.deadline_ms))
    except KeyboardInterrupt:
        pass
    except (OSError, websockets.ConnectionClosed) as error:
        raise SystemExit(f"Disconnected from the band: {error}") from None


if __name__ == "__main__":
    main()
