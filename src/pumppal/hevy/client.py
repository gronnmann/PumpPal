from __future__ import annotations

import time
from types import TracebackType

import httpx
from loguru import logger
from pydantic import SecretStr

from .models import (
    ExerciseTemplatesPage,
    RoutineModel,
    RoutinesPage,
    WorkoutModel,
    WorkoutsPage,
)

HEVY_BASE = "https://api.hevyapp.com"


class HevyClient:
    def __init__(self, api_key: SecretStr) -> None:
        self._client = httpx.AsyncClient(
            base_url=HEVY_BASE,
            headers={"api-key": api_key.get_secret_value()},
            timeout=30.0,
        )
        logger.debug("HevyClient initialised (base={})", HEVY_BASE)

    async def aclose(self) -> None:
        await self._client.aclose()
        logger.debug("HevyClient closed")

    async def __aenter__(self) -> HevyClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _get(self, path: str, **params: str | int | float | None) -> httpx.Response:
        t0 = time.perf_counter()
        logger.debug("Hevy GET {} params={}", path, params)
        r = await self._client.get(path, params=params if params else None)
        elapsed = (time.perf_counter() - t0) * 1000
        logger.debug(
            "Hevy GET {} -> {} ({:.0f} ms)",
            path,
            r.status_code,
            elapsed,
        )
        if r.is_error:
            logger.error(
                "Hevy API error: {} {} — body={!r}",
                r.status_code,
                path,
                r.text[:200],
            )
        r.raise_for_status()
        return r

    async def _put(self, path: str, json: object) -> httpx.Response:
        t0 = time.perf_counter()
        logger.debug("Hevy PUT {}", path)
        r = await self._client.put(path, json=json)
        elapsed = (time.perf_counter() - t0) * 1000
        logger.debug(
            "Hevy PUT {} -> {} ({:.0f} ms)",
            path,
            r.status_code,
            elapsed,
        )
        if r.is_error:
            logger.error(
                "Hevy API error: {} {} — body={!r}",
                r.status_code,
                path,
                r.text[:200],
            )
        r.raise_for_status()
        return r

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_workout(self, workout_id: str) -> WorkoutModel:
        r = await self._get(f"/v1/workouts/{workout_id}")
        workout = WorkoutModel.model_validate(r.json())
        logger.info(
            "Fetched workout: title={!r} exercises={} sets={} duration={}min",
            workout.title,
            len(workout.exercises),
            workout.total_sets,
            workout.duration_minutes,
        )
        return workout

    async def get_recent_workouts(
        self,
        page: int = 1,
        page_size: int = 10,
    ) -> WorkoutsPage:
        r = await self._get("/v1/workouts", page=page, pageSize=page_size)
        result = WorkoutsPage.model_validate(r.json())
        logger.debug(
            "Fetched workouts page={}/{} count={}",
            result.page,
            result.page_count,
            len(result.workouts),
        )
        return result

    async def get_routines(
        self,
        page: int = 1,
        page_size: int = 10,
    ) -> RoutinesPage:
        r = await self._get("/v1/routines", page=page, pageSize=page_size)
        result = RoutinesPage.model_validate(r.json())
        logger.debug(
            "Fetched routines page={}/{} count={}",
            result.page,
            result.page_count,
            len(result.routines),
        )
        return result

    async def get_routine(self, routine_id: str) -> RoutineModel:
        r = await self._get(f"/v1/routines/{routine_id}")
        routine = RoutineModel.model_validate(r.json()["routine"])
        logger.debug(
            "Fetched routine: id={} title={!r} exercises={}",
            routine.id,
            routine.title,
            len(routine.exercises),
        )
        return routine

    async def update_routine(
        self,
        routine_id: str,
        routine: RoutineModel,
    ) -> RoutineModel:
        logger.info(
            "Updating routine: id={} title={!r} exercises={}",
            routine_id,
            routine.title,
            len(routine.exercises),
        )
        payload = routine.model_dump(
            mode="json",
            by_alias=True,
            exclude={
                "id": True,
                "folder_id": True,
                "exercises": {
                    "__all__": {
                        "index": True,
                        "title": True,
                        "sets": {"__all__": {"index": True}},
                    }
                },
            },
        )
        r = await self._put(
            f"/v1/routines/{routine_id}",
            json={"routine": payload},
        )
        updated = RoutineModel.model_validate(r.json()["routine"][0])
        logger.info(
            "Routine updated successfully: id={} title={!r}",
            updated.id,
            updated.title,
        )
        return updated

    async def get_exercise_templates(
        self,
        page: int = 1,
        page_size: int = 100,
    ) -> ExerciseTemplatesPage:
        r = await self._get("/v1/exercise-templates", page=page, pageSize=page_size)
        result = ExerciseTemplatesPage.model_validate(r.json())
        logger.debug(
            "Fetched exercise templates page={}/{} count={}",
            result.page,
            result.page_count,
            len(result.exercise_templates),
        )
        return result
