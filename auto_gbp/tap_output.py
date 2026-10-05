"""Blue-note crossing tracker and timed, opt-in Windows keyboard output."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import heapq
import threading
import time
import math
import random
from collections import deque
from statistics import median
from statistics import NormalDist
from .runtime_log import AsyncLog


class CrossingTracker:
    def __init__(self, spacing=43.0, match_distance=55.0):
        self.spacing = spacing
        self.match_distance = match_distance
        self.tracks = []
        self.next_id = 1

    def update(self, notes, width, height, line_y, timestamp, arrival_y=None):
        sx, sy = width / 1600, height / 900
        tracks = [t for t in self.tracks if timestamp - t["time"] < 0.25]
        used = set()
        events = []
        for note in sorted(notes, key=lambda n: n["center_y"], reverse=True):
            x, y = note["center_x"] / sx, note["center_y"] / sy
            line = line_y / sy
            # Project the observed x onto the observation line using the same
            # approximate perspective width model as the visual detector.
            projected = 800 + (x - 800) * (5 + 0.36 * line) / (5 + 0.36 * y)
            lane_float = (projected - 800) / self.spacing + 3
            lane = round(lane_float)
            if not 0 <= lane < 7 or abs(lane_float - lane) > 0.48:
                continue
            candidates = [(abs(y - t["y"]), i, t) for i, t in enumerate(tracks)
                          if i not in used and t["lane"] == lane
                          and timestamp - t["time"] <= .12
                          and -8 <= y - t["y"] <= self.match_distance]
            if candidates:
                _, index, track = min(candidates, key=lambda item: item[0])
                used.add(index)
                if not track["fired"] and track["y"] < line <= y:
                    fraction = (line - track["y"]) / max(y - track["y"], 1e-6)
                    crossing_time = track["time"] + fraction * (timestamp - track["time"])
                    if arrival_y is not None:
                        velocity = (y - track["y"]) / max(timestamp - track["time"], 1e-6)
                        crossing_time += (arrival_y / sy - line) / max(velocity, 1e-6)
                    events.append((track["id"], lane, crossing_time))
                    track["fired"] = True
                track.update(y=y, time=timestamp)
                track['green_below'] = track.get('green_below', False) or note.get('green_below', False)
            else:
                # Notes first seen below the line are ignored, avoiding startup
                # or resumed-window late hits.
                tracks.append({"id": self.next_id, "lane": lane, "y": y,
                               "time": timestamp, "fired": y >= line,
                               "green_below": note.get('green_below', False)})
                used.add(len(tracks) - 1)
                self.next_id += 1
                track = tracks[-1]
            # Consecutive evidence avoids turning one occluded middle node
            # into a tail. Do not retain an old tail label through uncertainty.
            role = note.get('green_role', 'unknown')
            track['role_frames'] = track.get('role_frames', 0) + 1 if track.get('green_role') == role else 1
            track['green_role'] = role
        self.tracks = tracks
        return events


class DelayEstimator:
    """Conservative same-lane pairing; ambiguous or implausible matches are dropped."""
    def __init__(self, expected_ms=1000, tolerance_ms=250):
        self.expected_ms, self.tolerance_ms = expected_ms, tolerance_ms
        self.pending = [[] for _ in range(7)]
        self.samples = deque(maxlen=31)

    def add_top(self, events):
        for note_id, lane, timestamp in events:
            self.pending[lane].append((note_id, timestamp))

    def add_bottom(self, events):
        measured = []
        for _, lane, timestamp in events:
            candidates = [(note_id, top, (timestamp - top) * 1000)
                          for note_id, top in self.pending[lane]
                          if abs((timestamp - top) * 1000 - self.expected_ms) <= self.tolerance_ms]
            self.pending[lane] = [(note_id, top) for note_id, top in self.pending[lane]
                                  if (timestamp - top) * 1000 < self.expected_ms + self.tolerance_ms]
            if len(candidates) != 1:
                continue
            note_id, top, delay = candidates[0]
            self.pending[lane] = [item for item in self.pending[lane] if item[0] != note_id]
            self.samples.append(delay)
            measured.append((lane, delay))
        return measured

    def expire(self, now):
        for lane in range(7):
            self.pending[lane] = [(i, t) for i, t in self.pending[lane]
                                  if (now - t) * 1000 <= self.expected_ms + self.tolerance_ms]

    def summary(self):
        if not self.samples:
            return "travel:waiting"
        value = median(self.samples)
        mad = median(abs(s - value) for s in self.samples)
        return f"travel~{value:.0f}ms MAD:{mad:.0f} n:{len(self.samples)}"


class FlickTailMixin:
    def flick_tails(self, tracker, events, now):
        """Reserve visible connected tails before gap detection; consume once."""
        tails, ordinary = [], []
        associations = {}
        crossing_ids = {event[0] for event in events}
        for track in tracker.tracks:
            # A crossed/stale tail must not keep postponing another hold's end.
            if (not track.get('green_below') or now - track['time'] > .12
                    or (track['fired'] and track['id'] not in crossing_ids)):
                continue
            lane = track['lane']
            matches = [(identity, state) for identity, state in self.active.items()
                       if (abs(state['position'] - lane) <= .8 if 'position' in state else identity == lane)]
            if len(matches) == 1:
                identity, state = matches[0]
                associations[track['id']] = identity
                if state.get('flick_pending_id') != track['id']:
                    state['flick_pending_id'] = track['id']
                    state['flick_pending_until'] = track['time'] + self.flick_guard
        # Do not attach two simultaneous tails to the same hold.
        for note_id, lane, crossing in events:
            identity = associations.get(note_id)
            if identity is None or list(associations.values()).count(identity) != 1:
                ordinary.append((note_id, lane, crossing))
                continue
            state = self.active.pop(identity, None)
            if state is None:
                continue
            keys = state.get('keys', {identity: state['token']})
            tails.append((note_id, lane, crossing, dict(keys)))
        return ordinary, tails


class GreenHoldController(FlickTailMixin):
    """Same-lane prototype: node crossing starts, sustained ribbon gap ends.

    This does not classify hold endpoints semantically or follow lane changes.
    """
    def __init__(self, spacing=43, gap_ms=60, tail_release_ms=0, flick_guard_ms=250):
        self.tracker = CrossingTracker(spacing)
        self.gap = gap_ms / 1000
        self.tail_release = tail_release_ms / 1000
        self.flick_guard = flick_guard_ms / 1000
        self.active = {}
        self.sequence = 0

    def update(self, nodes, ribbon, width, height, line_y, now):
        events = self.tracker.update(nodes, width, height, line_y, now)
        transitions = []
        for _, lane, crossing in events:
            if lane not in self.active:
                self.sequence += 1
                token = f"H{self.sequence}"
                self.active[lane] = {"token": token, "last_node": crossing, "gap_start": None}
                transitions.append(("hold-down", token, lane, crossing))
            else:
                self.active[lane]["last_node"] = crossing
        for lane, state in list(self.active.items()):
            if ribbon[lane] or now < state.get('flick_pending_until', 0):
                state["gap_start"] = None
            elif state["gap_start"] is None:
                state["gap_start"] = now
            elif now - state["gap_start"] >= self.gap:
                tail = state["last_node"]
                # Never backdate release before the first missing frame.
                end = max(tail, state["gap_start"]) + self.tail_release
                transitions.append(("hold-up", state["token"], lane, end))
                del self.active[lane]
        return transitions


class SlidingHoldController(FlickTailMixin):
    """Hold an adjacent lane pair until the slide tail.

    Parallel ambiguous/merged ribbons are not guessed. Position is in lane units.
    """
    def __init__(self, spacing=43, gap_ms=60, overlap_ms=30, tail_release_ms=0, flick_guard_ms=250):
        self.tracker = CrossingTracker(spacing)
        self.gap = gap_ms / 1000
        self.overlap = overlap_ms / 1000
        self.flick_guard = flick_guard_ms / 1000
        self.tail_release = tail_release_ms / 1000
        self.active = {}
        self.sequence = 0

    def detach_spare_key(self, lane):
        """Give an obsolete retained lane to a new tap without releasing the path."""
        for state in self.active.values():
            if (lane in state['keys'] and lane != state['lane']
                    and abs(state['position'] - lane) >= .65):
                return state['keys'].pop(lane)
        return None

    def update(self, nodes, ribbon, width, height, line_y, now, positions=None):
        positions = list(positions or [])
        transitions = []
        claimed = set()
        occupied = {lane for state in self.active.values() for lane in state['keys']}
        # Only mutually unique matches can migrate. This avoids connecting one
        # hold to an unrelated neighbour when two ribbons converge or overlap.
        candidates = {identity: [i for i, p in enumerate(positions)
                                 if abs(p - state['position']) <= .9]
                      for identity, state in self.active.items()}
        for identity, state in list(self.active.items()):
            matches = candidates[identity]
            if len(matches) == 1 and sum(matches[0] in c for c in candidates.values()) == 1:
                index = matches[0]
                claimed.add(index)
                position = positions[index]
                state['position'] = position
                state['gap_start'] = None
            elif matches:
                # Ambiguous evidence is not proof of a tail. Retain state until
                # separation; scheduler watchdog still protects stuck keys.
                claimed.update(matches)
                state['gap_start'] = None
            elif now < state.get('flick_pending_until', 0):
                state['gap_start'] = None
            elif state['gap_start'] is None:
                state['gap_start'] = now

        crossings = self.tracker.update(nodes, width, height, line_y, now)
        tracks = {track['id']: track for track in self.tracker.tracks}
        for note_id, lane, crossing in crossings:
            track = tracks[note_id]
            is_tail = track.get('green_role') == 'tail' and track.get('role_frames', 0) >= 2
            related = [s for s in self.active.values() if s['lane'] == lane
                       or abs(s['position'] - lane) <= .8]
            if related:
                if len(related) == 1:
                    state = related[0]
                    state['last_node'] = crossing
                    state['gap_start'] = None
                    if is_tail:
                        # A terminal face can project into an adjacent lane on
                        # long ribbons. Keep existing key ownership; sustained
                        # ribbon absence still confirms release as before.
                        continue
                    # Ribbon movement only associates a path. A checkpoint face
                    # crossing the observation line determines the handoff time.
                    if lane in state['keys']:
                        state['lane'] = lane
                    elif lane not in occupied:
                        old_lane, old_token = state['lane'], state['token']
                        state['generation'] += 1
                        identity = old_token.split(':')[0]
                        state['token'] = f"{identity}:{state['generation']}"
                        state['lane'] = lane
                        occupied.add(lane)
                        transitions.append(('hold-down', state['token'], lane, crossing))
                        # Retain only a pair of adjacent lanes. Wider movement
                        # keeps the previous overlapping-handoff behaviour.
                        if max(*state['keys'], lane) - min(*state['keys'], lane) > 1:
                            for held_lane, held_token in state['keys'].items():
                                transitions.append(('handoff-up', held_token, held_lane, crossing + self.overlap))
                                occupied.discard(held_lane)
                            state['keys'].clear()
                        state['keys'][lane] = state['token']
                continue
            if is_tail:
                # Never start a new hold from an orphaned terminal face.
                continue
            self.sequence += 1
            token = f'S{self.sequence}:0'
            available = [p for i, p in enumerate(positions) if i not in claimed and abs(p - lane) <= .8]
            position = available[0] if len(available) == 1 else float(lane)
            self.active[self.sequence] = dict(token=token, generation=0, lane=lane,
                                             keys={lane: token},
                                             position=position, last_node=crossing,
                                             gap_start=None)
            transitions.append(('hold-down', token, lane, crossing))
        # Process this frame's crossings before confirming a gap: a visible
        # terminal/checkpoint crossing must never become a release + restart.
        for identity, state in list(self.active.items()):
            gap = state['gap_start']
            if gap is not None and now - gap >= self.gap:
                end = max(state['last_node'], gap) + self.tail_release
                for held_lane, held_token in state['keys'].items():
                    transitions.append(('hold-up', held_token, held_lane, end))
                del self.active[identity]
        return transitions


class Keyboard:
    def __init__(self, input_mode="vk"):
        if input_mode not in ("scan", "vk"):
            raise ValueError("input_mode 必须为 scan 或 vk")
        self.input_mode = input_mode
        ulong_ptr = ctypes.c_size_t

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                        ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                        ("dwExtraInfo", ulong_ptr)]

        class MOUSEINPUT(ctypes.Structure):
            _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                        ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                        ("time", wintypes.DWORD), ("dwExtraInfo", ulong_ptr)]

        class UNION(ctypes.Union):
            _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]

        class INPUT(ctypes.Structure):
            _fields_ = [("type", wintypes.DWORD), ("data", UNION)]

        self.INPUT, self.KEYBDINPUT = INPUT, KEYBDINPUT
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.user.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
        self.user.SendInput.restype = wintypes.UINT
        self.user.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
        self.user.MapVirtualKeyW.restype = wintypes.UINT
        self.user.GetForegroundWindow.restype = wintypes.HWND
        self.user.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        self.user.GetAncestor.restype = wintypes.HWND

    def focused(self, hwnd):
        return self.user.GetAncestor(self.user.GetForegroundWindow(), 2) == self.user.GetAncestor(hwnd, 2)

    def send(self, key, up=False):
        event = self.INPUT()
        event.type = 1
        vk = ord(key.upper())
        scan = self.user.MapVirtualKeyW(vk, 0)
        if not scan:
            raise RuntimeError(f"无法获取 {key} 的扫描码")
        flags = 2 if up else 0
        if self.input_mode == "scan":
            flags |= 8  # KEYEVENTF_SCANCODE; wVk must be zero in this mode.
            vk = 0
        # Even virtual-key mode supplies a valid scan code for comparison.
        event.data.ki = self.KEYBDINPUT(vk, scan, flags, 0, 0)
        ctypes.set_last_error(0)
        if self.user.SendInput(1, ctypes.byref(event), ctypes.sizeof(event)) != 1:
            error = ctypes.get_last_error()
            raise RuntimeError(f"SendInput 失败：WinError={error} mode={self.input_mode}；请检查权限等级")


class NormalTimingOffset:
    """Normal timing error in ms, conditioned on the supplied Great window."""
    def __init__(self, seed=None, mean_ms=5, sigma_ms=21.5):
        if not math.isfinite(mean_ms) or not -83 <= mean_ms <= 100:
            raise ValueError('jitter-mean-ms 必须位于 [-83,100] 且为有限数')
        if not math.isfinite(sigma_ms) or sigma_ms < 0:
            raise ValueError('jitter-sigma-ms 必须为非负有限数')
        self.random = random.Random(seed)
        self.mean, self.sigma = mean_ms, sigma_ms
        self.normal = NormalDist(mean_ms, sigma_ms) if sigma_ms else None
        self.low = self.normal.cdf(-83) if self.normal else 0
        self.high = self.normal.cdf(100) if self.normal else 1
        if self.high - self.low < 1e-12:
            raise ValueError('jitter-sigma-ms 过大，无法可靠采样')

    def perfect_probability(self):
        if not self.normal:
            return float(-33 <= self.mean <= 50)
        return (self.normal.cdf(50) - self.normal.cdf(-33)) / (self.high - self.low)

    def sample_ms(self):
        if not self.normal:
            return self.mean
        probability = self.low + self.random.random() * (self.high - self.low)
        probability = min(math.nextafter(1., 0.), max(math.nextafter(0., 1.), probability))
        return max(-83., min(100., self.normal.inv_cdf(probability)))


class TapScheduler:
    def __init__(self, hwnd, enabled=False, hold_ms=30, key_log=False, input_mode="vk", max_hold_ms=15000,
                 flick_hold_ms=30, timing_jitter=False, jitter_seed=None,
                 jitter_mean_ms=5, jitter_sigma_ms=21.5, output_backend=None):
        self.hwnd, self.enabled, self.hold = hwnd, enabled, hold_ms / 1000
        self.key_log = key_log
        self.max_hold = max_hold_ms / 1000
        self.flick_hold = flick_hold_ms / 1000
        self.jitter = NormalTimingOffset(jitter_seed, jitter_mean_ms, jitter_sigma_ms) if timing_jitter else None
        self.hold_offsets = {}
        self.held = {}
        self.keyboard = (output_backend if output_backend is not None else Keyboard(input_mode)) if enabled else None
        self.condition = threading.Condition()
        self.queue = []
        self.sequence = 0
        self.stopping = False
        self.pressed = {}
        self.error = None
        self.log = AsyncLog()
        self.enqueue_late = deque(maxlen=256)
        self.send_late = deque(maxlen=256)
        self.skipped_held = 0
        self.skipped_expired = 0
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def schedule(self, note_id, lane, deadline, release_token=None):
        with self.condition:
            deadline = self.offset_deadline(deadline, note_id)
            if release_token is not None:
                # Release and tap have one deadline/offset and deterministic order.
                self.sequence += 1
                heapq.heappush(self.queue, (deadline, self.sequence, 'handoff-up', 'asdfjkl'[lane], release_token))
            self.sequence += 1
            heapq.heappush(self.queue, (deadline, self.sequence, "down", "asdfjkl"[lane], note_id))
            self.condition.notify()

    def schedule_hold(self, action, token, lane, deadline, final=True, offset_bounds=None):
        if action not in ("hold-down", "hold-up", "handoff-up"):
            raise ValueError("非法长押动作")
        with self.condition:
            identity = str(token).split(':')[0]
            if identity not in self.hold_offsets:
                self.hold_offsets[identity] = self.jitter.sample_ms() if self.jitter else 0
            deadline = self.offset_deadline(deadline, token, self.hold_offsets[identity], offset_bounds)
            if action == 'hold-up' and final:
                self.hold_offsets.pop(identity, None)
            self.sequence += 1
            heapq.heappush(self.queue, (deadline, self.sequence, action, "asdfjkl"[lane], token))
            self.condition.notify()

    def schedule_flick(self, note_id, lane, deadline):
        with self.condition:
            deadline = self.offset_deadline(deadline, f'F{note_id}')
            self.sequence += 1
            heapq.heappush(self.queue, (deadline, self.sequence, "flick-down", "qweruio"[lane], f"F{note_id}"))
            self.condition.notify()

    def schedule_flick_tail(self, note_id, lane, deadline, keys, release_ms=30, offset_bounds=None):
        """Atomically queue a macro and all hold releases with one timing offset."""
        with self.condition:
            identity = next(iter(keys.values())).split(':')[0]
            offset = self.hold_offsets.pop(identity, 0)
            due = self.offset_deadline(deadline, f'FT{note_id}', offset, offset_bounds)
            self.sequence += 1
            heapq.heappush(self.queue, (due, self.sequence, 'flick-down', 'qweruio'[lane], f'FT{note_id}'))
            for held_lane, token in keys.items():
                self.sequence += 1
                heapq.heappush(self.queue, (due + release_ms / 1000, self.sequence,
                                           'hold-up', 'asdfjkl'[held_lane], token))
            self.condition.notify()

    def offset_deadline(self, deadline, token, offset=None, offset_bounds=None):
        if offset is None:
            offset = self.jitter.sample_ms() if self.jitter else 0
        if offset_bounds is not None:
            offset = max(offset_bounds[0], min(offset_bounds[1], offset))
        if self.jitter and self.key_log:
            self.log(f'[timing-offset] note={token} offset={offset:+.1f}ms')
        self.enqueue_late.append(max(0, (time.perf_counter() - deadline - offset / 1000) * 1000))
        return deadline + offset / 1000

    def timing_summary(self):
        with self.condition:
            enqueue, sent = list(self.enqueue_late), list(self.send_late)
        def p95(values):
            return sorted(values)[min(len(values) - 1, int(len(values) * .95))] if values else 0
        return (f'enqueue-late-p95:{p95(enqueue):.1f}ms send-late-p95:{p95(sent):.1f}ms '
                f'skip-held:{self.skipped_held} expired:{self.skipped_expired} log-dropped:{self.log.dropped}')

    def cancel(self):
        with self.condition:
            self.queue.clear()
            self.hold_offsets.clear()
            # Release events must survive cancellation.
            now = time.perf_counter()
            for key, token in self.pressed.items():
                self.sequence += 1
                heapq.heappush(self.queue, (now, self.sequence, "up", key, token))
            self.condition.notify()

    def adjust_delay(self, delta_ms):
        """Shift only pending key-down deadlines; preserve release times."""
        with self.condition:
            self.queue = [(due + delta_ms / 1000 if action in ("down", "hold-down", "hold-up", "handoff-up", "flick-down") else due,
                           sequence, action, key, token)
                          for due, sequence, action, key, token in self.queue]
            heapq.heapify(self.queue)
            self.condition.notify()

    def run(self):
        try:
            while True:
                with self.condition:
                    if self.stopping:
                        break
                    if not self.queue:
                        self.condition.wait()
                        continue
                    wait = self.queue[0][0] - time.perf_counter()
                    if wait > 0:
                        self.condition.wait(wait)
                        continue
                    due, _, action, key, token = heapq.heappop(self.queue)
                    if action in ("up", "hold-up", "handoff-up", "safety-up"):
                        if not self.enabled:
                            if action in ("hold-up", "handoff-up"):
                                self.log(f"[dry-run] {action} note={token} key={key}")
                            continue
                        if self.pressed.get(key) == token:
                            self.keyboard.send(key, up=True)
                            del self.pressed[key]
                            self.held.pop(key, None)
                            if action == "safety-up":
                                self.log(f"[hold-timeout] note={token} key={key} 强制释放")
                            if self.key_log:
                                self.log(f"[send-up] note={token} key={key} "
                                      f"late={max(0, (time.perf_counter() - due) * 1000):.1f}ms action={action}", flush=True)
                        continue
                    late = (time.perf_counter() - due) * 1000
                    if not self.enabled:
                        self.send_late.append(max(0, late))
                        self.log(f"[dry-run] action={action} note={token} key={key} late={late:.1f}ms")
                        continue
                    if not self.keyboard.focused(self.hwnd) or late > 100:
                        if late > 100:
                            self.skipped_expired += 1
                        self.log(f"[skip] note={token} key={key}: 未聚焦或过期")
                        continue
                    if key in self.held:
                        if action == 'hold-down' and str(token).startswith('S') and (
                                str(self.held[key]).split(':')[0] == str(token).split(':')[0]):
                            # Fast return before the previous overlap ended:
                            # keep the physical key down and transfer ownership.
                            self.pressed[key] = self.held[key] = token
                            self.sequence += 1
                            heapq.heappush(self.queue, (time.perf_counter() + self.max_hold,
                                                       self.sequence, 'safety-up', key, token))
                            if self.key_log:
                                self.log(f'[hold-transfer] note={token} key={key}')
                            continue
                        if self.key_log:
                            self.log(f"[skip-held] note={token} key={key}")
                        self.skipped_held += 1
                        continue
                    if key in self.pressed:
                        self.keyboard.send(key, up=True)
                        if self.key_log:
                            self.log(f"[send-up] note={self.pressed[key]} key={key} reason=retrigger")
                    self.keyboard.send(key)
                    self.send_late.append(max(0, (time.perf_counter() - due) * 1000))
                    if self.key_log:
                        self.log(f"[send-down] note={token} key={key} late={late:.1f}ms action={action}")
                    self.pressed[key] = token
                    self.sequence += 1
                    if action == "hold-down":
                        self.held[key] = token
                        heapq.heappush(self.queue, (time.perf_counter() + self.max_hold,
                                                   self.sequence, "safety-up", key, token))
                    else:
                        duration = self.flick_hold if action == "flick-down" else self.hold
                        heapq.heappush(self.queue, (time.perf_counter() + duration,
                                                   self.sequence, "up", key, token))
        except Exception as exc:
            self.error = str(exc)
            self.log(f"[send-error] {exc}")
        finally:
            if self.keyboard:
                for key in list(self.pressed):
                    try:
                        self.keyboard.send(key, up=True)
                        if self.key_log:
                            self.log(f"[send-up] note={self.pressed[key]} key={key} reason=shutdown")
                    except Exception as exc:
                        self.log(f"[send-error] key={key} release failed: {exc}")
                self.pressed.clear()

    def close(self):
        with self.condition:
            self.stopping = True
            self.condition.notify()
        # The transport must remain open until all shutdown releases finish.
        self.thread.join()
        self.log.close()
