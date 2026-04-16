from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request, status
from loguru import logger

from .hevy.models import HevyWebhookPayload
from .state import ConversationPhase, ConversationState, PendingWorkout

router = APIRouter()


@router.post("/webhook/hevy")
async def hevy_workout_webhook(
    request: Request,
    payload: HevyWebhookPayload,
    authorization: str = Header(default=""),
) -> dict[str, str]:
    # All shared objects are injected via app.state in main.py lifespan
    from .hevy.client import HevyClient

    app_state = request.app.state
    hevy_client: HevyClient = app_state.hevy_client
    tg_app: Any = app_state.tg_app  # PTB Application -- generic, use Any
    state: ConversationState = app_state.conv_state
    settings = app_state.settings

    logger.info("Webhook received -- workoutId={}", payload.workoutId)

    # Validate Authorization header
    expected: str = settings.hevy_webhook_secret.get_secret_value()
    if authorization != expected:
        logger.warning(
            "Webhook auth failed -- got={!r} (header mismatch)",
            authorization[:12] + "..." if len(authorization) > 12 else authorization,
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)

    async with state.lock:
        if state.phase not in (ConversationPhase.IDLE, ConversationPhase.CHATTING):
            logger.warning("Webhook ignored -- phase={} (not idle/chatting)", state.phase.name)
            return {"status": "busy"}

        old_phase = state.phase
        workout = await hevy_client.get_workout(payload.workoutId)
        pw = PendingWorkout(
            workout_id=workout.id,
            workout_title=workout.title,
            num_exercises=len(workout.exercises),
            total_sets=workout.total_sets,
            duration_minutes=workout.duration_minutes,
            workout_date=workout.start_time.date(),
        )
        state.pending_workout = pw
        state.reset_session()
        state.phase = ConversationPhase.AWAITING_USER_NOTES

    logger.info(
        "Webhook processed -- phase {} -> AWAITING_USER_NOTES "
        "workout={!r} exercises={} sets={} duration={}min",
        old_phase.name,
        pw.workout_title,
        pw.num_exercises,
        pw.total_sets,
        pw.duration_minutes,
    )

    msg = (
        f"Hey\\! I saw your workout *{_escape_md(pw.workout_title)}* "
        f"\\({pw.num_exercises} exercises, {pw.total_sets} sets, "
        f"{pw.duration_minutes} min\\)\\. "
        f"Anything to add before I start analysing?"
    )
    await tg_app.bot.send_message(
        chat_id=settings.telegram_chat_id,
        text=msg,
        parse_mode="MarkdownV2",
    )
    logger.debug("Telegram notification sent for workout={!r}", pw.workout_title)
    return {"status": "ok"}


def _escape_md(text: str) -> str:
    """Escape special characters for Telegram MarkdownV2."""
    special = r"_*[]()~`>#+-=|{}.!"
    return "".join(f"\\{c}" if c in special else c for c in text)
