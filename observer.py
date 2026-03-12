import json
import sys
import threading
import time
import urllib.request
from dataclasses import dataclass
from typing import Any

import brain_util as bu


@dataclass(frozen=True, slots=True)
class ObserverConfig:
    panel_url: str = "http://127.0.0.1:1236/route"
    sse_url: str = "http://127.0.0.1:1236/agent-events?agent=observer"
    agent: str = "observer"
    region: str = bu.SENTINEL
    scale: float = 1.0
    capture_width: int = 640
    capture_height: int = 640
    grid_size: int = 8
    grid_color: str = "rgba(0,255,200,0.95)"
    grid_stroke_width: int = 4
    swarm_agent: str = "swarm"
    sse_reconnect_delay: float = 1.0
    startup_delay: float = 1.0


OBSERVER_VLM: bu.VLMConfig = bu.VLMConfig(max_tokens=500)

SYSTEM_PROMPT: str = """\
Chess commentator. White bottom, Black top.
Columns a-h left-right. Rows 1-8 bottom-top.
1. Describe position in 2 sentences: active pieces, threats, checks, pins.
2. List 2-4 candidate White moves as: e2 e4 (from to). Brief reason each.
3. Flag any checkmate, capture, or fork clearly."""

USER_PROMPT: str = "Current position and best moves for White?"


def _parse_args() -> ObserverConfig:
    args: list[str] = sys.argv[1:]
    region: str = ObserverConfig.region
    scale: float = ObserverConfig.scale
    for i in range(len(args)):
        if args[i] == "--region" and i + 1 < len(args):
            region = args[i + 1]
        if args[i] == "--scale" and i + 1 < len(args):
            scale = float(args[i + 1])
    return ObserverConfig(region=region, scale=scale)


def _run_cycle(cfg: ObserverConfig, grid_overlays: list[dict[str, Any]]) -> None:
    bu.ui_pending(cfg.panel_url, cfg.agent, status=OBSERVER_VLM.model)

    raw_b64: str = bu.capture(
        cfg.panel_url, cfg.agent, cfg.region,
        cfg.capture_width, cfg.capture_height,
    )
    if raw_b64 == bu.SENTINEL:
        bu.ui_error(cfg.panel_url, cfg.agent, text="capture failed")
        return

    annotated_b64: str = bu.annotate(
        cfg.panel_url, cfg.agent, raw_b64, grid_overlays,
    )
    if annotated_b64 == bu.SENTINEL:
        annotated_b64 = raw_b64

    vlm_request: dict[str, Any] = bu.make_vlm_request_with_image(
        OBSERVER_VLM, SYSTEM_PROMPT, annotated_b64, USER_PROMPT,
    )

    text: str = bu.vlm_text(cfg.panel_url, cfg.agent, vlm_request)
    print(f"observer vlm response: {text[:120]}")

    bu.ui_done(
        cfg.panel_url, cfg.agent,
        text=text, image_b64=annotated_b64, status=OBSERVER_VLM.model,
    )

    bu.push(
        cfg.panel_url, cfg.agent, [cfg.swarm_agent],
        text=text, image_b64=annotated_b64,
    )
    print("observer: pushed to swarm")


def main() -> None:
    cfg: ObserverConfig = _parse_args()
    print(f"observer started region={cfg.region} scale={cfg.scale}")
    grid_overlays: list[dict[str, Any]] = bu.make_grid_overlays(
        cfg.grid_size, cfg.grid_color, cfg.grid_stroke_width,
    )

    cycle_done: threading.Event = threading.Event()

    def sse_listen() -> None:
        while True:
            try:
                with urllib.request.urlopen(cfg.sse_url, timeout=6000.0) as resp:
                    current_event: str = ""
                    for raw_line in resp:
                        line: str = raw_line.decode().rstrip("\r\n")
                        if line.startswith("event: "):
                            current_event = line[7:]
                        elif line.startswith("data: "):
                            if current_event == "message":
                                try:
                                    data: dict[str, Any] = json.loads(line[6:])
                                    if data.get("event_type") == "cycle_done":
                                        cycle_done.set()
                                except Exception:
                                    pass
                            current_event = ""
            except Exception:
                time.sleep(cfg.sse_reconnect_delay)

    threading.Thread(target=sse_listen, daemon=True).start()
    time.sleep(cfg.startup_delay)

    while True:
        cycle_done.clear()
        try:
            _run_cycle(cfg, grid_overlays)
        except Exception as e:
            print(f"observer error: {e}")
            bu.ui_error(cfg.panel_url, cfg.agent, text=f"ERROR: {e}")
            time.sleep(3.0)
            continue

        print("observer waiting for cycle_done...")
        cycle_done.wait()
        print("observer cycle_done received, starting next cycle")


if __name__ == "__main__":
    main()
