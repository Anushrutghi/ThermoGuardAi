"""Demo: exercise the WebSocket live-inspection channel like a phone browser.

Streams synthetic JPEG frames (simulating getUserMedia captures) and prints
the pipeline results. Usage:
    .venv/bin/python scripts/demo_ws_live.py --frames 5
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.core.security import create_access_token  # noqa: E402


def _frame(i: int) -> np.ndarray:
    """A synthetic electrical-panel-like frame with a hot zone."""
    frame = np.full((480, 640, 3), 55, dtype=np.uint8)
    frame[80:240, 60:180] = (30, 30, 35)
    frame[80:240, 220:340] = (28, 28, 32)
    frame[80:240, 380:500] = (32, 32, 36)
    frame[300:380, 420:520] = (235, 235, 235)  # bright hotspot
    frame = cv2.circle(frame, (440, 340), 22, (245, 245, 245), -1)
    return frame


async def main(frames: int) -> None:
    token = create_access_token(1, extra={"role": "admin", "username": "admin"})
    import websockets

    uri = f"ws://localhost:8000/api/v1/ws/inspect?token={token}"
    async with websockets.connect(uri) as ws:
        await ws.send(json.dumps({"type": "start_inspection", "mode": "continuous"}))
        ready = json.loads(await ws.recv())
        print("ready:", ready)

        for i in range(frames):
            img = _frame(i)
            ok, buf = cv2.imencode(".jpg", img)
            assert ok
            await ws.send(json.dumps({"type": "frame", "jpeg_base64": base64.b64encode(buf.tobytes()).decode()}))
            msg = json.loads(await ws.recv())
            if msg["type"] == "result":
                d = msg["data"]
                print(
                    f"frame {i}: risk={d['risk_score']:.0f} comps={d['component_count']} "
                    f"fps={d['fps']:.1f} thermal_max={d['thermal_stats']['max_temp'] if d['thermal_stats'] else 'n/a'} "
                    f"faults={[f['fault_type'] + ':' + f['severity'] for f in d['faults']][:3]}"
                )
            elif msg["type"] == "alarm":
                print("ALARM:", msg["severity"], "-", msg["message"][:80])

        await ws.send(json.dumps({"type": "stop_inspection", "notes": "demo"}))
        try:
            print("stopped:", await asyncio.wait_for(ws.recv(), timeout=5))
        except TimeoutError:
            print("stopped: (timeout waiting for ack)")
    print("demo complete ✅")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--frames", type=int, default=5)
    parser.add_argument("--host", default="localhost")
    args = parser.parse_args()
    asyncio.run(main(args.frames))
