"""Conservative, monotonic watchdog: BOTH UI and durable capture must stall."""
class StaleCaptureWatchdog:
    def __init__(self, now, timeout=600):
        self.timeout = timeout
        self.chunks = 0
        self.reset(now)

    def reset(self, now):
        self.last_ui = self.last_capture = now

    def ui_seen(self, now):
        self.last_ui = now

    def capture_seen(self, now, chunks):
        # Duplicate/final packets do not prove ongoing recording.
        if chunks > self.chunks:
            self.chunks = chunks
            self.last_capture = now

    def expired(self, now):
        return (now - self.last_ui >= self.timeout and
                now - self.last_capture >= self.timeout)
