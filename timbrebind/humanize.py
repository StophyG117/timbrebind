"""Human-like click timing."""

import math
import random


def _bounded(draw, lo, hi):
    """Resample until inside [lo, hi] so the edges don't pile up like a clamp."""
    for _ in range(30):
        v = draw()
        if lo <= v <= hi:
            return v
    return min(max(v, lo), hi)


def click_hold_s(cfg):
    """Button down->up time. Real clicks are log-normal-ish: a median near
    95 ms, most between ~70 and ~130 ms, and a thin tail of slower ones."""
    mu = math.log(cfg["hold_median_ms"] / 1000)
    return _bounded(lambda: random.lognormvariate(mu, cfg["hold_spread"]),
                    cfg["hold_min_ms"] / 1000, cfg["hold_max_ms"] / 1000)


class RepeatInterval:
    """Down-to-down interval for held-pedal repeats, bounded to [min, max].

    A scaled Beta(3, 4) gives a soft, slightly right-skewed hump (people drift
    late more than early) with no hard cut-off feel. A slow AR(1) tempo drift is
    layered on top so consecutive intervals are correlated the way a person's
    rhythm wanders, instead of being independent white noise.
    """

    def __init__(self, cfg):
        self.cfg = cfg
        self.drift = 0.0

    def reset(self):
        self.drift = random.gauss(0, 1.5 * self.cfg["repeat_drift_ms"] / 1000)

    def next(self):
        lo, hi = self.cfg["repeat_min_s"], self.cfg["repeat_max_s"]
        self.drift = 0.7 * self.drift + random.gauss(0, self.cfg["repeat_drift_ms"] / 1000)
        return _bounded(lambda: lo + (hi - lo) * random.betavariate(3, 4) + self.drift,
                        lo, hi)
