"""Bounded, non-blocking terminal logging for time-sensitive producers."""
import queue
import sys
import threading


class AsyncLog:
    def __init__(self, sink=None, capacity=2048):
        stream = sys.stdout
        self.sink = sink or (lambda message: print(message, file=stream, flush=True))
        self.queue = queue.Queue(maxsize=capacity)
        self.stopping = threading.Event()
        self.dropped = 0
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def __call__(self, message, **_):
        try:
            self.queue.put_nowait(message)
        except queue.Full:
            self.dropped += 1

    def run(self):
        while not self.stopping.is_set() or not self.queue.empty():
            try:
                message = self.queue.get(timeout=.02)
            except queue.Empty:
                continue
            try:
                self.sink(message)
            except Exception:
                self.dropped += 1

    def close(self):
        self.stopping.set()
        self.thread.join(timeout=.5)
