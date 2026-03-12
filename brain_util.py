import json
import urllib.request
from dataclasses import dataclass, fields
from typing import Any


SENTINEL: str = "NONE"
NORM: int = 1000


@dataclass(frozen=True, slots=True)
class VLMConfig:
    model: str = "qwen3.5-0.8b"
    temperature: float = 0.7
    max_tokens: int = 200
    top_p: float = 0.8
    top_k: int = 20
    min_p: float = 0.0
    stream: bool = False
    presence_penalty: float = 1.5
    frequency_penalty: float = 0.0
    repeat_penalty: float = 1.0
    stop: list[str] | None = None
    seed: int | None = None
    logit_bias: dict[str, float] | None = None


def _vlm_params(cfg: VLMConfig) -> dict[str, Any]:
    params: dict[str, Any] = {}
    for f in fields(cfg):
        v: Any = getattr(cfg, f.name)
        if v is not None:
            params[f.name] = v
    return params


def route(
    panel_url: str,
    agent: str,
    recipients: list[str],
    timeout: float = 120.0,
    **payload: Any,
) -> dict[str, Any]:
    body: dict[str, Any] = {"agent": agent, "recipients": recipients}
    body.update(payload)
    req: urllib.request.Request = urllib.request.Request(
        panel_url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def capture(
    panel_url: str, agent: str, region: str,
    width: int, height: int, timeout: float = 30.0,
) -> str:
    resp: dict[str, Any] = route(
        panel_url, agent, ["capture"],
        timeout=timeout, region=region, capture_size=[width, height],
    )
    return resp.get("image_b64", SENTINEL)


def annotate(
    panel_url: str, agent: str,
    image_b64: str, overlays: list[dict[str, Any]],
    timeout: float = 10.0,
) -> str:
    resp: dict[str, Any] = route(
        panel_url, agent, ["annotate"],
        timeout=timeout, image_b64=image_b64, overlays=overlays,
    )
    return resp.get("image_b64", SENTINEL)


def vlm(
    panel_url: str, agent: str,
    vlm_request: dict[str, Any], timeout: float = 360.0,
) -> dict[str, Any]:
    return route(
        panel_url, agent, ["vlm"],
        timeout=timeout, vlm_request=vlm_request,
    )


def vlm_text(
    panel_url: str, agent: str,
    vlm_request: dict[str, Any], timeout: float = 360.0,
) -> str:
    resp: dict[str, Any] = vlm(panel_url, agent, vlm_request, timeout)
    choices: list[Any] = resp.get("choices", [])
    if not choices:
        return SENTINEL
    return choices[0].get("message", {}).get("content", SENTINEL)


def screen(
    panel_url: str, agent: str, region: str,
    actions: list[dict[str, Any]], timeout: float = 30.0,
) -> None:
    route(
        panel_url, agent, ["screen"],
        timeout=timeout, region=region, actions=actions,
    )


def push(
    panel_url: str, agent: str,
    recipients: list[str], timeout: float = 10.0,
    **payload: Any,
) -> None:
    route(panel_url, agent, recipients, timeout=timeout, **payload)


def ui_pending(
    panel_url: str, agent: str, status: str = "",
) -> None:
    push(panel_url, agent, ["ui"], event_type="pending", status=status)


def ui_done(
    panel_url: str, agent: str,
    text: str = SENTINEL, image_b64: str = SENTINEL,
    status: str = "",
) -> None:
    push(
        panel_url, agent, ["ui"],
        event_type="done", text=text, image_b64=image_b64, status=status,
    )


def ui_error(
    panel_url: str, agent: str, text: str,
) -> None:
    push(panel_url, agent, ["ui"], event_type="error", text=text)


def make_grid_overlays(
    grid_size: int, color: str, stroke_width: int,
) -> list[dict[str, Any]]:
    overlays: list[dict[str, Any]] = []
    step: int = NORM // grid_size
    for i in range(grid_size + 1):
        pos: int = i * step
        overlays.append({
            "type": "overlay",
            "points": [[pos, 0], [pos, NORM]],
            "closed": False,
            "stroke": color,
            "stroke_width": stroke_width,
        })
        overlays.append({
            "type": "overlay",
            "points": [[0, pos], [NORM, pos]],
            "closed": False,
            "stroke": color,
            "stroke_width": stroke_width,
        })
    return overlays


def make_arrow_overlay(
    from_col: int, from_row: int, to_col: int, to_row: int,
    color: str, grid_size: int, stroke_width: int = 8,
) -> dict[str, Any]:
    step: int = NORM // grid_size
    fx: int = from_col * step + step // 2
    fy: int = from_row * step + step // 2
    tx: int = to_col * step + step // 2
    ty: int = to_row * step + step // 2
    return {
        "type": "overlay",
        "points": [[fx, fy], [tx, ty]],
        "closed": False,
        "stroke": color,
        "stroke_width": stroke_width,
    }


def grid_to_norm(col: int, row: int, grid_size: int) -> tuple[int, int]:
    step: int = NORM // grid_size
    return col * step + step // 2, row * step + step // 2


def make_vlm_request(
    cfg: VLMConfig,
    system_prompt: str,
    user_content: str | list[dict[str, Any]],
) -> dict[str, Any]:
    params: dict[str, Any] = _vlm_params(cfg)
    params["messages"] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]
    return params


def make_vlm_request_with_image(
    cfg: VLMConfig,
    system_prompt: str,
    image_b64: str,
    user_text: str,
) -> dict[str, Any]:
    params: dict[str, Any] = _vlm_params(cfg)
    params["messages"] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
            {"type": "text", "text": user_text},
        ]},
    ]
    return params
