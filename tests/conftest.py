"""Shared test fixtures use the standalone synthetic detector."""

from core.demo import (
    ScriptedDetector as FakeDetector,
    black_frames,
    make_person,
    standing_keypoints,
)

__all__ = ["FakeDetector", "black_frames", "make_person", "standing_keypoints"]
