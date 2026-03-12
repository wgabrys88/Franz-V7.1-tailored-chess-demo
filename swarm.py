import json
import re
import sys
import threading
import time
import urllib.request
from dataclasses import dataclass
from typing import Any

import brain_util as bu


COLUMNS: dict[str, int] = {"a": 0, "b": 1, "c": 2, "d": 3, "e": 4, "f": 5, "g": 6, "h": 7}
ROWS: dict[str, int] = {"1": 7, "2": 6, "3": 5, "4": 4, "5": 3, "6": 2, "7": 1, "8": 0}

_VLM_THROTTLE: threading.Semaphore = threading.Semaphore(3)


@dataclass(frozen=True, slots=True)
class SwarmConfig:
    panel_url: str = "http://127.0.0.1:1236/route"
    sse_url: str = "http://127.0.0.1:1236/agent-events?agent=swarm"
    agent: str = "swarm"
    region: str = bu.SENTINEL
    scale: float = 1.0
    capture_width: int = 640
    capture_height: int = 640
    grid_size: int = 8
    grid_color: str = "rgba(0,255,200,0.95)"
    grid_stroke_width: int = 4
    observer_agent: str = "observer"
    sse_reconnect_delay: float = 1.0


SPECIALIST_VLM: bu.VLMConfig = bu.VLMConfig(max_tokens=220)
EXECUTOR_VLM: bu.VLMConfig = bu.VLMConfig(max_tokens=170)

_PIECE_PROMPT: str = """\
White {piece} move specialist.
Given the analysis, find the best {PIECE} move for White.
Reply: from to or NONE. No other text."""

_TACTICS_PROMPT: str = """\
White tactics specialist: captures, forks, pins, skewers, checkmate.
Given the analysis, find the best tactical move for White (any piece).
Reply: from to or NONE. No other text."""

_POSITIONAL_PROMPT: str = """\
White positional specialist: development, center control, piece activity.
Given the analysis, find the best positional move for White (any piece).
Reply: from to or NONE. No other text."""

SPECIALISTS: list[tuple[str, str, str]] = [
    ("pawn", "#3ecf8e", _PIECE_PROMPT.format(piece="pawn", PIECE="PAWN")),
    ("knight", "#4a9eff", _PIECE_PROMPT.format(piece="knight", PIECE="KNIGHT")),
    ("bishop", "#c084fc", _PIECE_PROMPT.format(piece="bishop", PIECE="BISHOP")),
    ("rook", "#f0a000", _PIECE_PROMPT.format(piece="rook", PIECE="ROOK")),
    ("queen", "#ff4455", _PIECE_PROMPT.format(piece="queen", PIECE="QUEEN")),
    ("king", "#06b6d4", _PIECE_PROMPT.format(piece="king", PIECE="KING")),
    ("tactics", "#f97316", _TACTICS_PROMPT),
    ("positional", "#a3e635", _POSITIONAL_PROMPT),
]

EXECUTOR_SYSTEM_PROMPT: str = """\
Pick the best move from the colored arrows on the board.
Reply ONLY: from to. Columns a-h left-right. Rows 1-8 bottom-top.
No other text."""

EXECUTOR_USER_PROMPT: str = "Pick the best move from the arrows."


def _parse_args() -> SwarmConfig:
    args: list[str] = sys.argv[1:]
    region: str = SwarmConfig.region
    scale: float = SwarmConfig.scale
    for i in range(len(args)):
        if args[i] == "--region" and i + 1 < len(args):
            region = args[i + 1]
        if args[i] == "--scale" and i + 1 < len(args):
            scale = float(args[i + 1])
    return SwarmConfig(region=region, scale=scale)


def _parse_chess_move(text: str) -> tuple[int, int, int, int] | None:
    pattern = re.compile(r'\b([a-h])([1-8])\s+([a-h])([1-8])\b', re.IGNORECASE)
    for line in text.strip().splitlines():
        m = pattern.search(line.strip())
        if m:
            fc: int | None = COLUMNS.get(m.group(1).lower())
            fr: int | None = ROWS.get(m.group(2))
            tc: int | None = COLUMNS.get(m.group(3).lower())
            tr: int | None = ROWS.get(m.group(4))
            if fc is not None and fr is not None and tc is not None and tr is not None:
                return fc, fr, tc, tr
    return None


def _move_to_notation(col: int, row: int) -> str:
    return f"{chr(ord('a') + col)}{8 - row}"


def _run_specialist(
    name: str, system_prompt: str, observer_text: str, cfg: SwarmConfig,
) -> tuple[str, tuple[int, int, int, int] | None]:
    vlm_request: dict[str, Any] = bu.make_vlm_request(
        SPECIALIST_VLM, system_prompt, observer_text,
    )
    _VLM_THROTTLE.acquire()
    try:
        text: str = bu.vlm_text(cfg.panel_url, cfg.agent, vlm_request)
        print(f"  specialist {name}: {text.strip()[:80]}")
        move: tuple[int, int, int, int] | None = _parse_chess_move(text)
        if move:
            n1: str = _move_to_notation(move[0], move[1])
            n2: str = _move_to_notation(move[2], move[3])
            print(f"  specialist {name}: parsed {n1} -> {n2}")
        return name, move
    except Exception as e:
        print(f"  specialist {name} error: {e}")
        return name, None
    finally:
        _VLM_THROTTLE.release()


def _run_executor(
    proposals: list[tuple[str, str, tuple[int, int, int, int]]],
    cfg: SwarmConfig,
) -> tuple[int, int, int, int] | None:
    grid_overlays: list[dict[str, Any]] = bu.make_grid_overlays(
        cfg.grid_size, cfg.grid_color, cfg.grid_stroke_width,
    )
    arrow_overlays: list[dict[str, Any]] = []
    for name, color, move in proposals:
        arrow_overlays.append(
            bu.make_arrow_overlay(move[0], move[1], move[2], move[3], color, cfg.grid_size)
        )
        n1: str = _move_to_notation(move[0], move[1])
        n2: str = _move_to_notation(move[2], move[3])
        print(f"  executor overlay: {name} ({color}) {n1}->{n2}")

    all_overlays: list[dict[str, Any]] = grid_overlays + arrow_overlays

    raw_b64: str = bu.capture(
        cfg.panel_url, cfg.agent, cfg.region,
        cfg.capture_width, cfg.capture_height,
    )
    if raw_b64 == bu.SENTINEL:
        print("  executor: capture failed")
        return None

    annotated_b64: str = bu.annotate(
        cfg.panel_url, cfg.agent, raw_b64, all_overlays,
    )
    if annotated_b64 == bu.SENTINEL:
        annotated_b64 = raw_b64

    vlm_request: dict[str, Any] = bu.make_vlm_request_with_image(
        EXECUTOR_VLM, EXECUTOR_SYSTEM_PROMPT, annotated_b64, EXECUTOR_USER_PROMPT,
    )

    _VLM_THROTTLE.acquire()
    try:
        text: str = bu.vlm_text(cfg.panel_url, cfg.agent, vlm_request)
        print(f"  executor response: {text.strip()[:80]}")

        bu.ui_done(
            cfg.panel_url, cfg.agent,
            text=text, image_b64=annotated_b64, status=EXECUTOR_VLM.model,
        )

        return _parse_chess_move(text)
    except Exception as e:
        print(f"  executor error: {e}")
        return None
    finally:
        _VLM_THROTTLE.release()


def _execute_drag(
    from_col: int, from_row: int, to_col: int, to_row: int, cfg: SwarmConfig,
) -> None:
    fx, fy = bu.grid_to_norm(from_col, from_row, cfg.grid_size)
    tx, ty = bu.grid_to_norm(to_col, to_row, cfg.grid_size)
    n1: str = _move_to_notation(from_col, from_row)
    n2: str = _move_to_notation(to_col, to_row)
    print(f"  drag: {n1}->{n2} norm({fx},{fy})->({tx},{ty})")
    bu.screen(cfg.panel_url, cfg.agent, cfg.region, [{
        "type": "drag",
        "x1": fx, "y1": fy,
        "x2": tx, "y2": ty,
    }])
    print(f"  drag executed")


def _signal_observer_done(cfg: SwarmConfig) -> None:
    bu.push(
        cfg.panel_url, cfg.agent, [cfg.observer_agent],
        event_type="cycle_done",
    )
    print("  signaled observer: cycle_done")


def _handle_observation(observer_text: str, observer_image: str, cfg: SwarmConfig) -> None:
    print(f"swarm received observation ({len(observer_text)} chars)")
    for line in observer_text.strip().splitlines():
        print(f"  {line}")

    bu.ui_pending(cfg.panel_url, cfg.agent, status="specialists running")

    results: list[tuple[str, tuple[int, int, int, int] | None]] = []
    results_lock: threading.Lock = threading.Lock()

    def thread_fn(name: str, system_prompt: str) -> None:
        result: tuple[str, tuple[int, int, int, int] | None] = _run_specialist(
            name, system_prompt, observer_text, cfg,
        )
        with results_lock:
            results.append(result)

    threads: list[threading.Thread] = []
    for name, color, system_prompt in SPECIALISTS:
        t: threading.Thread = threading.Thread(
            target=thread_fn, args=(name, system_prompt), daemon=True,
        )
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    proposals: list[tuple[str, str, tuple[int, int, int, int]]] = []
    color_map: dict[str, str] = {name: color for name, color, _ in SPECIALISTS}
    for name, move in results:
        if move is not None:
            proposals.append((name, color_map[name], move))

    print(f"swarm collected {len(proposals)} proposals from {len(results)} specialists")

    if not proposals:
        print("swarm: no valid proposals, signaling observer")
        bu.ui_done(cfg.panel_url, cfg.agent, text="no proposals", status="idle")
        _signal_observer_done(cfg)
        return

    if len(proposals) == 1:
        name, color, move = proposals[0]
        n1: str = _move_to_notation(move[0], move[1])
        n2: str = _move_to_notation(move[2], move[3])
        print(f"swarm: single proposal from {name}: {n1}->{n2}, executing directly")
        bu.ui_done(cfg.panel_url, cfg.agent, text=f"{name}: {n1}->{n2}", status="executing")
        _execute_drag(move[0], move[1], move[2], move[3], cfg)
        _signal_observer_done(cfg)
        return

    bu.ui_pending(cfg.panel_url, cfg.agent, status="executor picking")
    picked: tuple[int, int, int, int] | None = _run_executor(proposals, cfg)

    if picked is not None:
        n1 = _move_to_notation(picked[0], picked[1])
        n2 = _move_to_notation(picked[2], picked[3])
        print(f"swarm: executor picked {n1}->{n2}")
        bu.ui_done(cfg.panel_url, cfg.agent, text=f"picked: {n1}->{n2}", status="executing")
        _execute_drag(picked[0], picked[1], picked[2], picked[3], cfg)
    else:
        name, color, move = proposals[0]
        n1 = _move_to_notation(move[0], move[1])
        n2 = _move_to_notation(move[2], move[3])
        print(f"swarm: executor failed, falling back to {name}: {n1}->{n2}")
        bu.ui_done(cfg.panel_url, cfg.agent, text=f"fallback {name}: {n1}->{n2}", status="executing")
        _execute_drag(move[0], move[1], move[2], move[3], cfg)

    _signal_observer_done(cfg)


def main() -> None:
    cfg: SwarmConfig = _parse_args()
    print(f"swarm started region={cfg.region} scale={cfg.scale}")
    busy: threading.Lock = threading.Lock()

    def handle_message(data: dict[str, Any]) -> None:
        text: str = data.get("text", bu.SENTINEL)
        image: str = data.get("image_b64", bu.SENTINEL)
        if text == bu.SENTINEL:
            return
        if not busy.acquire(blocking=False):
            print("swarm busy, skipping")
            return
        try:
            _handle_observation(text, image, cfg)
        except Exception as e:
            print(f"swarm cycle error: {e}")
            try:
                bu.ui_error(cfg.panel_url, cfg.agent, text=f"ERROR: {e}")
                _signal_observer_done(cfg)
            except Exception:
                pass
        finally:
            busy.release()

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
                                    threading.Thread(
                                        target=handle_message, args=(data,), daemon=True,
                                    ).start()
                                except Exception:
                                    pass
                            current_event = ""
            except Exception:
                time.sleep(cfg.sse_reconnect_delay)

    threading.Thread(target=sse_listen, daemon=True).start()
    while True:
        time.sleep(3600.0)


if __name__ == "__main__":
    main()
