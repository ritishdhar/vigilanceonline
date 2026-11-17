"""Normalize detection observations into incident records."""

from dataclasses import dataclass, field
from typing import Optional

FIGHT_SCENES = ("FIGHT", "MULTI_PERSON_FIGHT", "GROUP_ASSAULT")


@dataclass
class Incident:
    key: str  # start + scene: one notification per incident
    title: str
    scene: Optional[str]
    alerts: list = field(default_factory=list)
    timestamp: float = 0.0


def incident_from_state(
    scene: Optional[str], alerts: dict, timestamp: float, event_id: Optional[str] = None
) -> Optional[Incident]:
    """Decide whether the current frame state deserves a notification."""
    names = sorted(alerts or {})
    fight = scene is not None and scene in FIGHT_SCENES
    if not fight and not names:
        return None
    if fight:
        title = {
            "FIGHT": "Possible fight detected",
            "MULTI_PERSON_FIGHT": "Group fight detected",
            "GROUP_ASSAULT": "Group assault detected",
        }.get(scene, "Fight detected")
    else:
        title = "Alert: " + ", ".join(names)
    key = event_id or f"{round(float(timestamp), 3)}:{scene or ','.join(names)}"
    return Incident(
        key=key, title=title, scene=scene, alerts=names, timestamp=float(timestamp)
    )
