"""Named shows: a show is a list of DIY slots PLAYed together (one slot = a still,
several = a ~1 frame/s loop). Persisted as JSON so `next` and the gesture setup
survive restarts.

  {"order": ["logo", "spiral", "session", "virgil"], "current": "logo",
   "shows": {"logo": {"slots": [1]}, "spiral": {"slots": [2, 3, ...]}, ...}}

Path: explicit, $MASKAPI_SHOWS, else ~/.maskapi/shows.json.
"""
from __future__ import annotations

import copy
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from .protocol import DIY_SLOTS, FACE_MAX_IDS, check_slot

log = logging.getLogger("maskapi")
NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")

# the layout currently on the mask (diy.py SHOWS / CYCLE)
DEFAULT = {
    "order": ["logo", "spiral", "session", "virgil"],
    "current": None,
    "shows": {
        "logo": {"slots": [1]},
        "spiral": {"slots": list(range(2, 11))},
        "virgil": {"slots": [11]},
        "session": {"slots": list(range(12, 21))},
    },
}


def default_path() -> Path:
    return Path(os.environ.get("MASKAPI_SHOWS") or Path.home() / ".maskapi" / "shows.json")


class ShowError(ValueError):
    pass


class ShowRegistry:
    def __init__(self, path: Optional[os.PathLike] = None):
        self.path = Path(path) if path else default_path()
        self.data = self._read()

    def _read(self) -> dict:
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return copy.deepcopy(DEFAULT)
        d.setdefault("shows", {})
        d.setdefault("order", list(d["shows"]))
        d.setdefault("current", None)
        return d

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    # ---- queries ----

    @property
    def shows(self) -> Dict[str, dict]:
        return self.data["shows"]

    @property
    def order(self) -> List[str]:
        return [n for n in self.data["order"] if n in self.shows] + \
               [n for n in self.shows if n not in self.data["order"]]

    @property
    def current(self) -> Optional[str]:
        return self.data.get("current")

    def to_json(self) -> dict:
        return {"path": str(self.path), "order": self.order, "current": self.current,
                "shows": copy.deepcopy(self.shows)}

    def get(self, name: str) -> dict:
        try:
            return self.shows[name]
        except KeyError:
            raise ShowError(f"unknown show {name!r} (have: {', '.join(self.shows) or 'none'})") from None

    def slots(self, name: str) -> List[int]:
        return list(self.get(name)["slots"])

    def overlaps(self, slots: Sequence[int], exclude: Optional[str] = None) -> Dict[str, List[int]]:
        want = set(slots)
        return {n: sorted(want & set(s["slots"])) for n, s in self.shows.items()
                if n != exclude and want & set(s["slots"])}

    def free_run(self, n: int, exclude: Optional[str] = None) -> Optional[int]:
        """Lowest start slot of n consecutive slots not used by other shows."""
        used = {s for name, sh in self.shows.items() if name != exclude for s in sh["slots"]}
        for start in DIY_SLOTS:
            run = range(start, start + n)
            if run[-1] <= DIY_SLOTS[-1] and not used & set(run):
                return start
        return None

    def plan(self, name: str, n_frames: int, start_slot: Optional[int] = None, force: bool = False) -> List[int]:
        """Slots a new/redefined show would use. Overlap with another show raises ShowError
        unless force (those shows are then dropped by define())."""
        if not NAME_RE.match(name or ""):
            raise ShowError("show name must be 1-32 of A-Z a-z 0-9 _ -")
        if n_frames < 1:
            raise ShowError("a show needs at least one frame")
        if start_slot is None:
            start_slot = self.shows[name]["slots"][0] if name in self.shows else self.free_run(n_frames, name)
            if start_slot is None:
                raise ShowError(f"no {n_frames} free consecutive slots; give start_slot (and force)")
        slots = list(range(start_slot, start_slot + n_frames))
        check_slot(slots[0])
        check_slot(slots[-1])
        clash = self.overlaps(slots, exclude=name)
        if clash and not force:
            span = f"slot {slots[0]}" if len(slots) == 1 else f"slots {slots[0]}-{slots[-1]}"
            raise ShowError(f"{span} overlap " +
                            ", ".join(f"{n} {s}" for n, s in clash.items()) + " (use force to replace them)")
        return slots

    # ---- updates (each saves) ----

    def define(self, name: str, slots: Sequence[int], force: bool = False, **meta) -> List[str]:
        """Record a show; returns names of shows dropped because their slots were overwritten."""
        slots = [check_slot(s) for s in slots]
        clash = self.overlaps(slots, exclude=name)
        if clash and not force:
            raise ShowError(f"slots overlap {clash}")
        for n in clash:
            log.warning("show %r dropped: its slots %s were overwritten by %r", n, clash[n], name)
            del self.shows[n]
            if self.current == n:
                self.data["current"] = None
        self.shows[name] = {"slots": slots, "defined": int(time.time()), **meta}
        if name not in self.data["order"]:
            self.data["order"].append(name)
        self.data["order"] = [n for n in self.data["order"] if n in self.shows]
        self.save()
        return list(clash)

    def remove(self, name: str):
        self.get(name)
        del self.shows[name]
        self.data["order"] = [n for n in self.data["order"] if n != name]
        if self.current == name:
            self.data["current"] = None
        self.save()

    def set_order(self, names: Sequence[str]):
        for n in names:
            self.get(n)
        self.data["order"] = list(names) + [n for n in self.order if n not in names]
        self.save()

    def set_current(self, name: str):
        self.get(name)
        self.data["current"] = name
        self.save()

    def next_name(self) -> str:
        order = self.order
        if not order:
            raise ShowError("no shows defined")
        cur = self.current
        return order[(order.index(cur) + 1) % len(order)] if cur in order else order[0]

    def gesture_ids(self) -> List[int]:
        """First slot of each show, in cycle order (FACE takes at most 7 ids)."""
        ids = [self.shows[n]["slots"][0] for n in self.order]
        if len(ids) > FACE_MAX_IDS:
            raise ShowError(f"FACE takes at most {FACE_MAX_IDS} ids; {len(ids)} shows")
        return ids
