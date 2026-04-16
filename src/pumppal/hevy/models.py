from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class SetModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    index: int
    set_type: str = Field(alias="type", default="normal")
    weight_kg: float | None = None
    reps: int | None = None
    rpe: float | None = None
    distance_meters: float | None = None
    duration_seconds: float | None = None


class ExerciseModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    index: int
    title: str
    notes: str | None = None
    exercise_template_id: str
    superset_id: int | None = None
    sets: list[SetModel]


class WorkoutModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    title: str
    description: str | None = None
    start_time: datetime
    end_time: datetime
    routine_id: str | None = None
    exercises: list[ExerciseModel]

    @property
    def duration_minutes(self) -> int:
        return int((self.end_time - self.start_time).total_seconds() // 60)

    @property
    def total_sets(self) -> int:
        return sum(len([s for s in e.sets if s.set_type != "warmup"]) for e in self.exercises)


class WorkoutsPage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    workouts: list[WorkoutModel]
    page: int
    page_count: int


class RepRange(BaseModel):
    model_config = ConfigDict(extra="ignore")

    start: int
    end: int


class RoutineSetModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    index: int
    set_type: str = Field(alias="type", default="normal")
    weight_kg: float | None = None
    reps: int | None = None
    rep_range: RepRange | None = None


class RoutineExerciseModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    index: int
    title: str
    exercise_template_id: str
    superset_id: int | None = None
    rest_seconds: int | None = None
    notes: str | None = None
    sets: list[RoutineSetModel]


class RoutineModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    title: str
    folder_id: int | None = None
    exercises: list[RoutineExerciseModel]


class RoutinesPage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    routines: list[RoutineModel]
    page: int
    page_count: int


class ExerciseTemplateModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    title: str
    primary_muscle_group: str | None = None
    secondary_muscle_groups: list[str] = []


class ExerciseTemplatesPage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    exercise_templates: list[ExerciseTemplateModel]
    page: int
    page_count: int


class HevyWebhookPayload(BaseModel):
    workoutId: str
