"""Generate runtime assets: alarm sound WAV and logo SVG (no binaries in git)."""
from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOUND_DIR = ROOT / "assets" / "sounds"
ASSET_DIR = ROOT / "assets"


def generate_alarm_sound(path: Path, duration: float = 1.2, freq: float = 880, sample_rate: int = 22050) -> None:
    """Write a two-tone alarm WAV (works on macOS/Linux/Windows)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = bytearray()
    n = int(sample_rate * duration)
    for i in range(n):
        t = i / sample_rate
        # alternating 880/660 Hz pulses for an attention-grabbing alarm
        tone = freq if (int(t * 4) % 2 == 0) else freq * 0.75
        envelope = 0.6 * math.exp(-0.8 * t)
        value = int(32767 * envelope * math.sin(2 * math.pi * tone * t))
        frames += struct.pack("<h", value)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(bytes(frames))
    print(f"Alarm sound written: {path}")


def generate_logo(path: Path) -> None:
    """Write a minimal SVG logo."""
    svg = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 80">
  <rect width="320" height="80" rx="12" fill="#0E4DA4"/>
  <text x="18" y="52" font-family="Arial, sans-serif" font-size="34" font-weight="bold" fill="#ffffff">⚡</text>
  <text x="62" y="50" font-family="Arial, sans-serif" font-size="26" font-weight="bold" fill="#ffffff">ThermoGuard</text>
  <text x="62" y="68" font-family="Arial, sans-serif" font-size="12" fill="#cfe0f5" letter-spacing="2">AI</text>
</svg>"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(svg)
    print(f"Logo written: {path}")


def main() -> None:
    generate_alarm_sound(SOUND_DIR / "alarm.wav")
    generate_logo(ASSET_DIR / "logo.svg")


if __name__ == "__main__":
    main()
