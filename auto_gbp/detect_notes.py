"""Extract blue notes, green slide nodes/strips and pink flick candidates.

This is deliberately a single-frame diagnostic tool. It produces artifacts that
make false positives and missed notes easy to inspect before real-time tracking.
"""

from __future__ import annotations

import argparse
import json
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np


REFERENCE_WIDTH = 1600
REFERENCE_HEIGHT = 900


def read_image(path: Path) -> np.ndarray:
    # imdecode handles non-ASCII Windows paths more reliably than imread.
    data = np.fromfile(str(path), dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"无法读取图片: {path}")
    return image


def write_image(path: Path, image: np.ndarray) -> None:
    ok, encoded = cv2.imencode(path.suffix, image)
    if not ok:
        raise ValueError(f"无法编码图片: {path}")
    encoded.tofile(str(path))


def playfield_mask(shape: tuple[int, ...], top: float, bottom: float) -> np.ndarray:
    height, width = shape[:2]
    sx = width / REFERENCE_WIDTH
    sy = height / REFERENCE_HEIGHT
    top_y = round(top * sy)
    bottom_y = round(bottom * sy)

    # The lanes converge near the top centre. These bounds intentionally keep
    # the score, character UI, speakers and bottom touch effects outside.
    def edges(y_ref: float) -> tuple[int, int]:
        half_width = 40 + 1.03 * y_ref
        return round((800 - half_width) * sx), round((800 + half_width) * sx)

    left_top, right_top = edges(top)
    left_bottom, right_bottom = edges(bottom)
    polygon = np.array(
        [[left_top, top_y], [right_top, top_y],
         [right_bottom, bottom_y], [left_bottom, bottom_y]],
        dtype=np.int32,
    )
    mask = np.zeros((height, width), dtype=np.uint8)
    cv2.fillConvexPoly(mask, polygon, 255)
    return mask


def detect(image: np.ndarray, hue_low: int, hue_high: int,
           min_note_y: int, hsv: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray, list[dict]]:
    height, width = image.shape[:2]
    scale_x = width / REFERENCE_WIDTH
    scale_y = height / REFERENCE_HEIGHT
    field = playfield_mask(image.shape, top=34, bottom=720)

    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV) if hsv is None else hsv
    # Blue/cyan tap notes have a saturated core; the rim is restored for display.
    color = cv2.inRange(hsv, (hue_low, 90, 95), (hue_high, 255, 255))
    color = cv2.bitwise_and(color, field)
    kept_mask, notes = extract_markers(color, min_note_y)
    return field, kept_mask, notes


def extract_markers(raw: np.ndarray, min_note_y: int = 38,
                    frame_shape: tuple | None = None,
                    offset: tuple[int, int] = (0, 0), *,
                    build_mask: bool = True) -> tuple[np.ndarray | None, list[dict]]:
    """Horizontal note faces; perspective constrains their expected width."""
    height, width = (frame_shape or raw.shape)[:2]
    sx, sy = width / REFERENCE_WIDTH, height / REFERENCE_HEIGHT
    horizontal = cv2.getStructuringElement(cv2.MORPH_RECT, (max(3, round(7 * sx)), 1))
    filtered = cv2.morphologyEx(raw, cv2.MORPH_OPEN, horizontal)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(filtered, 8)
    kept = np.zeros_like(raw) if build_mask else None
    markers = []
    for label in range(1, count):
        x, y, w, h, area = map(int, stats[label])
        yr, wr, hr = (y + offset[1]) / sy, w / sx, h / sy
        expected_width = 5 + 0.36 * yr
        if yr < min_note_y or not 0.60 * expected_width <= wr <= 1.65 * expected_width:
            continue
        if not 2 <= hr <= 60 or wr / max(hr, 1) < 2.3 or area < 10 * sx * sy:
            continue
        # Restrict label comparison to its bounding box, not the full frame.
        if kept is not None:
            region = kept[y:y + h, x:x + w]
            region[labels[y:y + h, x:x + w] == label] = 255
        markers.append({"x": x + offset[0], "y": y + offset[1], "width": w, "height": h,
                        "center_x": round(x + offset[0] + w / 2, 1),
                        "center_y": round(y + offset[1] + h / 2, 1), "mask_area": area})
    markers.sort(key=lambda item: item["center_y"], reverse=True)
    return kept, markers


def classify_yellow_connections(markers, green, offset=(0, 0)):
    """Look outside the face for a green ribbon, not inside its yellow rim."""
    for note in markers:
        cx = round(note["center_x"] - offset[0])
        y = note["y"] - offset[1]
        w, h = note["width"], note["height"]
        half = max(1, round(w * .22))
        margin, depth = max(2, round(h * .25)), max(3, round(w * .4))
        evidence = []
        for start, end in ((y - margin - depth, y - margin),
                           (y + h + margin, y + h + margin + depth)):
            patch = green[max(0, start):max(0, min(green.shape[0], end)),
                          max(0, cx - half):min(green.shape[1], cx + half + 1)]
            evidence.append(bool(patch.size and np.count_nonzero(patch) / patch.size >= .3))
        note["green_above"], note["green_below"] = evidence
        note["hold_connected"] = any(evidence)


def classify_green_roles(markers, green, offset=(0, 0), valid=None):
    """Require complete samples on both sides; an ROI edge is not a tail."""
    for note in markers:
        cx = round(note['center_x'] - offset[0])
        y = note['y'] - offset[1]
        w, h = note['width'], note['height']
        half = max(2, round(w * .3))
        margin, depth = max(2, round(h * .25)), max(3, round(w * .25))
        evidence = []
        for start, end in ((y - margin - depth, y - margin),
                           (y + h + margin, y + h + margin + depth)):
            left, right = cx - half, cx + half + 1
            if start < 0 or end > green.shape[0] or left < 0 or right > green.shape[1]:
                evidence.append(None)
                continue
            patch = green[start:end, left:right]
            if valid is not None and not np.all(valid[start:end, left:right]):
                evidence.append(None)
                continue
            coverage = np.count_nonzero(patch) / patch.size
            evidence.append(True if coverage >= .3 else False if coverage <= .08 else None)
        above, below = evidence
        note['green_role'] = ('unknown' if None in evidence else
                              'middle' if above and below else
                              'head' if above else 'tail' if below else 'unknown')


@lru_cache(maxsize=16)
def _top_geometry(height: int, width: int, top: float, bottom: float):
    """Bounded, read-only geometry cache shared by frames of the same size."""
    sx, sy = width / 1600, height / 900
    y1, y2 = round(top * sy), min(height, round(bottom * sy) + 1)
    half = 40 + 1.03 * bottom
    x1, x2 = max(0, round((800 - half) * sx)), min(width, round((800 + half) * sx) + 1)
    if x2 <= x1 or y2 <= y1:
        raise ValueError("图像过小，顶部区域为空")
    field = np.zeros((y2 - y1, x2 - x1), np.uint8)
    polygon = np.array([[round((800 - 40 - 1.03 * top) * sx) - x1, 0],
                        [round((800 + 40 + 1.03 * top) * sx) - x1, 0],
                        [x2 - x1 - 1, y2 - y1 - 1], [0, y2 - y1 - 1]], np.int32)
    cv2.fillConvexPoly(field, polygon, 255)
    field.flags.writeable = False
    return x1, y1, x2, y2, field


def analyze_top_frame(image: np.ndarray, top: float = 34, bottom: float = 200,
                      observation_y: float = 160, judgment_y: float = 738.52,
                      render: bool = True, green_holds: bool = False,
                      lane_spacing: float = 43, flicks: bool = False) -> tuple[dict, dict]:
    """Detect note faces only in a small top ROI; all coordinates remain frame-relative.

    Fixed lines are calibration, not observed evidence. No hold association or
    flick direction is inferred from this early observation band.
    """
    if not 0 <= top < bottom <= 900 or not top <= observation_y < bottom or not 0 <= judgment_y < 900:
        raise ValueError("顶部范围/观察线/底部判定线参数不合法")
    height, width = image.shape[:2]
    sx, sy = width / 1600, height / 900
    x1, y1, x2, y2, field = _top_geometry(height, width, top, bottom)
    roi = image[y1:y2, x1:x2]
    if roi.size == 0:
        raise ValueError("图像过小，顶部区域为空")
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    if render:
        overlay, features, filtered = image.copy(), np.zeros_like(image), np.zeros_like(image)
    groups = {}
    for name, prefix, low, high, color in [
        ("blue_notes", "B", (85, 90, 95), (110, 255, 255), (255, 210, 0)),
        ("yellow_notes", "Y", (15, 100, 170), (34, 255, 255), (0, 230, 255)),
        ("green_nodes", "G", (35, 60, 190), (65, 255, 255), (40, 255, 90)),
        ("flick_notes", "F", (145, 60, 185), (179, 255, 255), (210, 70, 255)),
    ]:
        if (not render and name not in ("blue_notes", "yellow_notes")
                and not (green_holds and name == "green_nodes")
                and not (flicks and name == "flick_notes")):
            groups[name] = []
            continue
        raw = cv2.bitwise_and(cv2.inRange(hsv, low, high), field)
        kept, markers = extract_markers(raw, top, image.shape, (x1, y1), build_mask=render)
        if render:
            filtered[y1:y2, x1:x2][kept > 0] = roi[kept > 0]
        for index, note in enumerate(markers, 1):
            x, y, w, h = note["x"], note["y"], note["width"], note["height"]
            if name == "flick_notes":
                note["direction"] = "unknown"
            if render:
                cv2.rectangle(features, (x, y), (x + w, y + h), color, -1)
                cv2.rectangle(overlay, (x, y), (x + w, y + h), color, 1)
                cv2.putText(overlay, f"{prefix}{index}", (x, max(12, y - 3)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.4, color, 1)
        groups[name] = markers
    green_support = cv2.bitwise_and(cv2.inRange(hsv, (35, 65, 85), (85, 255, 255)), field)
    classify_yellow_connections(groups["yellow_notes"], green_support, (x1, y1))
    classify_green_roles(groups['green_nodes'] + groups['yellow_notes'], green_support, (x1, y1), field)
    classify_yellow_connections(groups["flick_notes"], green_support, (x1, y1))
    for canvas in ((features, overlay, filtered) if render else ()):
        cv2.rectangle(canvas, (x1, y1), (x2 - 1, y2 - 1), (100, 100, 100), 1)
        cv2.line(canvas, (x1, round(observation_y * sy)), (x2 - 1, round(observation_y * sy)), (0, 200, 255), 1)
        cv2.line(canvas, (0, round(judgment_y * sy)), (width - 1, round(judgment_y * sy)), (100, 100, 100), 1)
    result = {"image_width": width, "image_height": height, **groups,
              "green_segments": [], "detection_region": [x1, y1, x2 - x1, y2 - y1],
              "observation_line": {"y": observation_y * sy, "source": "configured"},
              "judgment_line": {"y": judgment_y * sy, "source": "configured"}}
    if green_holds:
        # Sample only a thin strip at the observation line, not the complete
        # path. Each lane uses a narrow centre window to reduce neighbour bleed.
        green = green_support
        yy = round(observation_y * sy) - y1
        # Continuous ribbon positions retain between-lane motion. Fixed lane
        # probes alone mistake a diagonal leaving a lane for a hold ending.
        strip = green[max(0, yy - 1):min(green.shape[0], yy + 2)]
        columns = np.flatnonzero(np.mean(strip > 0, axis=0) >= .5) if strip.size else np.array([], dtype=int)
        runs = np.split(columns, np.flatnonzero(np.diff(columns) > 1) + 1) if len(columns) else []
        result["green_positions"] = [float(((x1 + (run[0] + run[-1]) / 2) / sx - 800) / lane_spacing + 3)
                                     for run in runs if len(run) >= max(2, round(4 * sx))]
        ribbon = []
        for lane in range(7):
            xx = round((800 + (lane - 3) * lane_spacing) * sx) - x1
            half_width = max(1, round(lane_spacing * .2 * sx))
            patch = green[max(0, yy - 1):min(green.shape[0], yy + 2),
                          max(0, xx - half_width):min(green.shape[1], xx + half_width + 1)]
            ribbon.append(bool(patch.size and np.count_nonzero(patch) / patch.size >= .35))
        result["green_ribbon"] = ribbon
    return result, ({"08_features.png": features, "04_overlay.png": overlay,
                     "06_relevant_only.png": filtered} if render else {})


def detect_bottom_blue(image: np.ndarray, top: float = 650, bottom: float = 775):
    """Blue faces near the hit line; ROI processing avoids the animated centre."""
    height, width = image.shape[:2]
    sy = height / 900
    y1, y2 = round(top * sy), min(height, round(bottom * sy) + 1)
    roi = image[y1:y2]
    if roi.size == 0:
        return []
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    raw = cv2.inRange(hsv, (85, 90, 95), (110, 255, 255))
    _, markers = extract_markers(raw, top, image.shape, (0, y1), build_mask=False)
    return markers


def draw_bottom_keys(canvas: np.ndarray, judgment_y: float, positions: list[float]):
    """Emulator-style key badges, calibrated in the 1600x900 game coordinates."""
    sx, sy = canvas.shape[1] / 1600, canvas.shape[0] / 900
    y = round(judgment_y * sy)
    radius = max(6, round(12 * sx))
    for letter, position in zip("asdfjkl", positions):
        x = round(position * sx)
        cv2.circle(canvas, (x, y), radius, (90, 90, 90), -1)
        cv2.circle(canvas, (x, y), radius, (220, 220, 220), 1)
        font_scale = max(.3, .45 * sx)
        (w, h), _ = cv2.getTextSize(letter.upper(), cv2.FONT_HERSHEY_SIMPLEX, font_scale, 1)
        cv2.putText(canvas, letter.upper(), (x - w // 2, y + h // 2),
                    cv2.FONT_HERSHEY_SIMPLEX, font_scale, (255, 255, 255), 1, cv2.LINE_AA)


def detect_green(image: np.ndarray, hsv: np.ndarray | None = None) -> tuple[np.ndarray, list[dict], list[dict]]:
    height, width = image.shape[:2]
    sx = width / REFERENCE_WIDTH
    sy = height / REFERENCE_HEIGHT
    field = playfield_mask(image.shape, top=34, bottom=720)
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV) if hsv is None else hsv
    raw = cv2.inRange(hsv, (35, 65, 85), (85, 255, 255))
    raw = cv2.bitwise_and(raw, field)
    bright = cv2.inRange(hsv, (35, 60, 190), (65, 255, 255))
    bright = cv2.bitwise_and(bright, field)
    kept, markers = extract_markers(bright)
    classify_green_roles(markers, raw, valid=field)
    segments = []
    # Connect neighbouring green nodes only when an actual green strip is
    # present between them. These are candidate associations, not touch events.
    ordered = sorted(markers, key=lambda item: item["center_y"])
    for upper, lower in zip(ordered, ordered[1:]):
        y1, y2 = round(upper["center_y"]), round(lower["center_y"])
        if y2 - y1 < 6 * sy:
            continue
        polygon = np.array([
            [upper["x"], y1], [upper["x"] + upper["width"], y1],
            [lower["x"] + lower["width"], y2], [lower["x"], y2],
        ], np.int32)
        support = np.zeros_like(raw)
        cv2.fillConvexPoly(support, polygon, 255)
        area = cv2.countNonZero(support)
        coverage = cv2.countNonZero(cv2.bitwise_and(raw, support)) / max(area, 1)
        if coverage < 0.40:
            continue
        kept |= cv2.bitwise_and(raw, support)
        segments.append({"polygon": polygon.tolist(), "green_coverage": round(coverage, 3)})
    return kept, markers, segments


def detect_flick(image: np.ndarray, hsv: np.ndarray | None = None) -> tuple[np.ndarray, list[dict]]:
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV) if hsv is None else hsv
    field = playfield_mask(image.shape, top=34, bottom=720)
    raw = cv2.inRange(hsv, (145, 60, 185), (179, 255, 255))
    raw = cv2.bitwise_and(raw, field)
    kept, markers = extract_markers(raw)
    for note in markers:
        x, y, w, h = note["x"], note["y"], note["width"], note["height"]
        # Preserve nearby chevrons above the accepted face, not all pink pixels
        # on character clothing. Direction is deliberately left unclassified.
        top = max(0, y - round(w * 0.65))
        kept[top:y + h, x:x + w] |= raw[top:y + h, x:x + w]
        note["arrow_region"] = [x, top, w, y - top]
        note["direction"] = "unknown"
    return kept, markers


def detect_judgment_line(image: np.ndarray, hsv: np.ndarray | None = None) -> tuple[np.ndarray, dict | None]:
    """Find the long cyan horizontal band in the lower gameplay area.

    No fixed y-coordinate is returned if the observed band is absent. Short
    notes and wide touch glows are rejected by horizontal coverage/thickness.
    """
    height, width = image.shape[:2]
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV) if hsv is None else hsv
    cyan = cv2.inRange(hsv, (85, 45, 170), (115, 255, 255))
    cyan[:round(height * 0.55)] = 0
    cyan[round(height * 0.95):] = 0
    # Lane markers interrupt the line about every one-seventh of the screen.
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(3, round(width * 0.06)), 1))
    horizontal = cv2.morphologyEx(cyan, cv2.MORPH_OPEN, kernel)
    coverage = np.count_nonzero(horizontal, axis=1) / width
    rows = np.flatnonzero(coverage >= 0.55)
    kept = np.zeros_like(cyan)
    if len(rows) == 0:
        return kept, None
    bands = np.split(rows, np.flatnonzero(np.diff(rows) > 1) + 1)
    bands = [band for band in bands if len(band) <= max(3, round(height * 0.02))]
    if not bands:
        return kept, None
    band = max(bands, key=lambda item: float(coverage[item].max()))
    # Centre the near-maximum support rows; weak outer glow does not shift y.
    peak_rows = band[coverage[band] >= coverage[band].max() * 0.95]
    y = float(np.average(peak_rows, weights=coverage[peak_rows]))
    columns = np.flatnonzero(np.any(horizontal[band] > 0, axis=0))
    x1, x2 = int(columns[0]), int(columns[-1])
    kept[band] = horizontal[band]
    return kept, {"y": round(y, 2), "y_normalized": round(y / height, 6),
                  "x_start": x1, "x_end": x2,
                  "band_top": int(band[0]), "band_bottom": int(band[-1]),
                  "horizontal_coverage": round(float(coverage[band].max()), 4)}


def analyze_frame(image: np.ndarray, hue_low: int = 85,
                  hue_high: int = 110, min_note_y: int = 40,
                  features_only: bool = False, render: bool = True) -> tuple[dict, dict]:
    """Analyze an in-memory BGR frame without disk I/O."""
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    field, mask, notes = detect(image, hue_low, hue_high, min_note_y, hsv)
    _, yellow_mask, yellow_notes = detect(image, 15, 34, min_note_y, hsv)
    classify_yellow_connections(yellow_notes, cv2.bitwise_and(
        cv2.inRange(hsv, (35, 65, 85), (85, 255, 255)), field))
    green_mask, green_nodes, green_segments = detect_green(image, hsv)
    pink_mask, flick_notes = detect_flick(image, hsv)
    classify_yellow_connections(flick_notes, cv2.bitwise_and(
        cv2.inRange(hsv, (35, 65, 85), (85, 255, 255)), field))
    judgment_mask, judgment_line = detect_judgment_line(image, hsv)

    result = {"image_width": image.shape[1], "image_height": image.shape[0],
              "blue_notes": notes, "yellow_notes": yellow_notes,
              "green_nodes": green_nodes, "green_segments": green_segments,
              "flick_notes": flick_notes, "judgment_line": judgment_line}
    if not render:
        return result, {}

    if not features_only:
        playfield = cv2.bitwise_and(image, image, mask=field)
        rim = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        notes_only = cv2.bitwise_and(image, image, mask=cv2.dilate(mask, rim))
        relevant_mask = mask | green_mask | pink_mask | yellow_mask
        relevant_only = cv2.bitwise_and(image, image, mask=cv2.dilate(relevant_mask, rim))

    overlay = image.copy()
    features = np.zeros_like(image)
    for segment in green_segments:
        polygon = np.array(segment["polygon"], np.int32)
        cv2.fillConvexPoly(features, polygon, (0, 90, 0))
    for prefix, objects, color in [("B", notes, (255, 210, 0)),
                                    ("Y", yellow_notes, (0, 230, 255)),
                                    ("G", green_nodes, (40, 255, 90)),
                                    ("F", flick_notes, (210, 70, 255))]:
        for index, note in enumerate(objects, 1):
            x, y, w, h = note["x"], note["y"], note["width"], note["height"]
            cv2.rectangle(overlay, (x, y), (x + w, y + h), color, 2)
            cv2.rectangle(features, (x, y), (x + w, y + h), color, -1)
            cv2.putText(overlay, f"{prefix}{index}", (x, max(18, y - 6)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2)
    # Actual arrow pixels retain their observed orientation in the feature view.
    for note in flick_notes:
        x, top, w, h = note["arrow_region"]
        crop = pink_mask[top:top + h, x:x + w]
        features[top:top + h, x:x + w][crop > 0] = (210, 70, 255)

    if judgment_line is not None:
        y = round(judgment_line["y"])
        start, end = (judgment_line["x_start"], y), (judgment_line["x_end"], y)
        for canvas in (overlay, features):
            cv2.line(canvas, start, end, (0, 200, 255), 2)
        cv2.putText(overlay, f"Judgment y={judgment_line['y']:.2f}",
                    (20, max(20, y - 15)), cv2.FONT_HERSHEY_SIMPLEX,
                    0.65, (0, 200, 255), 2)

    artifacts = {"08_features.png": features} if features_only else dict([
        ("01_playfield.png", playfield),
        ("02_color_mask.png", mask),
        ("03_notes_only.png", notes_only),
        ("04_overlay.png", overlay),
        ("05_green_mask.png", green_mask),
        ("06_relevant_only.png", relevant_only),
        ("07_flick_mask.png", pink_mask),
        ("08_features.png", features),
        ("09_judgment_mask.png", judgment_mask),
    ])
    result = {"image_width": image.shape[1],
              "image_height": image.shape[0], "blue_notes": notes, "yellow_notes": yellow_notes,
              "green_nodes": green_nodes, "green_segments": green_segments,
              "flick_notes": flick_notes, "judgment_line": judgment_line}
    return result, artifacts


def process_image(source: Path, output: Path, hue_low: int = 85,
                  hue_high: int = 110, min_note_y: int = 40) -> dict:
    result, artifacts = analyze_frame(read_image(source), hue_low, hue_high, min_note_y)
    output.mkdir(parents=True, exist_ok=True)
    for name, artifact in artifacts.items():
        write_image(output / name, artifact)
    result["source"] = str(source)
    notes, green_nodes = result["blue_notes"], result["green_nodes"]
    green_segments, flick_notes = result["green_segments"], result["flick_notes"]
    judgment_line = result["judgment_line"]
    (output / "notes.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{source.name}: {len(notes)} 个蓝色音符候选、{len(green_nodes)} 个绿色节点、"
          f"{len(green_segments)} 段绿色路径、{len(flick_notes)} 个 flick 候选，"
          f"{len(result['yellow_notes'])} 个黄色音符候选，结果位于 {output.resolve()}")
    print(f"  判定线 y={judgment_line['y']:.2f}" if judgment_line else "  未检测到判定线")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path, help="游戏截图文件或图片目录")
    parser.add_argument("--output", type=Path, default=Path("output"))
    parser.add_argument("--hue-low", type=int, default=85, help="OpenCV HSV 蓝色下界，默认 85")
    parser.add_argument("--hue-high", type=int, default=110, help="OpenCV HSV 蓝色上界，默认 110")
    parser.add_argument("--min-note-y", type=int, default=40,
                        help="按 1600×900 基准计的蓝色音符最上方检测位置，默认 40")
    args = parser.parse_args()
    if not 0 <= args.hue_low <= args.hue_high <= 179:
        parser.error("色相范围必须满足 0 <= hue-low <= hue-high <= 179")
    if not 0 <= args.min_note_y < 720:
        parser.error("min-note-y 必须位于 [0, 720) 内")
    if not args.image.exists():
        parser.error(f"输入路径不存在: {args.image}")
    batch = args.image.is_dir()
    sources = sorted(p for p in args.image.iterdir()
                     if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".bmp", ".webp"}) if batch else [args.image]
    if not sources:
        parser.error("输入目录中没有可读取的图片")
    for source in sources:
        destination = args.output / source.stem if batch else args.output
        process_image(source, destination, args.hue_low, args.hue_high, args.min_note_y)


if __name__ == "__main__":
    main()
