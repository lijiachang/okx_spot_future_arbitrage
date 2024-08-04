import logging
import time


logger = logging.getLogger(__name__)


class QpsCalculator:
    def __init__(self, name, interval=60):
        self.name = name
        self.interval = interval
        self.counter = 0
        self.reset_at = time.time()

    def incr(self, number=1):
        self.counter += 1
        now = time.time()
        if now - self.reset_at > self.interval:
            logger.info(f"QPS for {self.name}: {self.counter / (now - self.reset_at)}")
            self.reset()

    def reset(self):
        self.reset_at = time.time()
        self.counter = 0
