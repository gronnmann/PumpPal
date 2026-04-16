from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date
from enum import Enum, auto

from pydantic_ai.messages import ModelMessage


class ConversationPhase(Enum):
    IDLE = auto()
    AWAITING_WORKOUT_SELECTION = auto()  # /analyze: waiting for user to pick a workout
    AWAITING_USER_NOTES = auto()
    AGENT_RUNNING = auto()
    CHATTING = auto()


@dataclass
class PendingWorkout:
    workout_id: str
    workout_title: str
    num_exercises: int
    total_sets: int
    duration_minutes: int
    workout_date: date


@dataclass
class ConversationState:
    phase: ConversationPhase = field(default=ConversationPhase.IDLE)
    pending_workout: PendingWorkout | None = field(default=None)
    workout_options: list[PendingWorkout] = field(default_factory=list)
    message_history: list[ModelMessage] = field(default_factory=list)
    user_notes: str = field(default="")
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False, compare=False)

    @property
    def lock(self) -> asyncio.Lock:
        return self._lock

    def reset_session(self) -> None:
        """Reset chat history and notes. Called when a new workout starts or /clear."""
        self.message_history = []
        self.user_notes = ""

    def clear(self) -> None:
        """Full reset to IDLE. Used by /clear command."""
        self.phase = ConversationPhase.IDLE
        self.pending_workout = None
        self.workout_options = []
        self.reset_session()


_state: ConversationState | None = None


def get_state() -> ConversationState:
    global _state
    if _state is None:
        _state = ConversationState()
    return _state
