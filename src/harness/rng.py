"""The seeded generator tree: one random stream per (scenario, seed, day, stream name).

Each stream is derived from its key alone, so:
- a day can be simulated on its own and match the same day inside a range run;
- drawing from one stream (say "failures") never shifts another ("deployments");
- the policy is not part of the key, so every policy on a scenario faces the
  same draws (common random numbers).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json

import numpy as np


class RandomStreams:
    def __init__(self, scenario: str, seed: int):
        if seed < 0:
            raise ValueError(f"seed must be a non-negative integer, got {seed}")
        self.scenario = scenario
        self.seed = seed

    def generator(self, day: dt.date, stream: str) -> np.random.Generator:
        """A fresh generator for the stream; the same key always gives the same draws."""
        key = json.dumps([self.scenario, day.isoformat(), stream]).encode()
        digest = hashlib.sha256(key).digest()
        words = tuple(int.from_bytes(digest[i:i + 4], "little") for i in range(0, len(digest), 4))
        return np.random.default_rng(np.random.SeedSequence(self.seed, spawn_key=words))
