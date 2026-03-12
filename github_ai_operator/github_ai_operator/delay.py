from __future__ import annotations

import random
import time
from dataclasses import dataclass

from .config import DelayProfile


@dataclass
class HumanPacer:
    profile: DelayProfile
    rng: random.Random

    def _sleep(self, low: float, high: float, label: str) -> None:
        amount = self.rng.uniform(low, high) + self.rng.uniform(0, self.profile.jitter_seconds)
        print(f'[pace] {label}: sleeping {amount:.2f}s')
        time.sleep(amount)

    def before_search(self) -> None:
        self._sleep(self.profile.min_search_seconds, self.profile.max_search_seconds, 'before_search')

    def before_clone(self) -> None:
        self._sleep(self.profile.min_clone_seconds, self.profile.max_clone_seconds, 'before_clone')

    def before_issue(self) -> None:
        self._sleep(self.profile.min_issue_seconds, self.profile.max_issue_seconds, 'before_issue')
