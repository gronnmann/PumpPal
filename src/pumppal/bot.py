from __future__ import annotations

from typing import Any, cast

from loguru import logger
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .agent import AgentDeps, run_analysis, run_followup
from .hevy.client import HevyClient
from .settings import Settings
from .state import ConversationPhase, ConversationState, PendingWorkout

# PTB Application is a 6-param generic -- alias with Any to avoid verbose annotations
AnyApplication = Application[Any, Any, Any, Any, Any, Any]

_BOT_DATA_MODEL = "model_name"
_BOT_DATA_CLIENT = "hevy_client"
_BOT_DATA_SETTINGS = "settings"
_BOT_DATA_STATE = "state"


def build_bot(
    settings: Settings,
    hevy_client: HevyClient,
    state: ConversationState,
    model_name: str,
) -> AnyApplication:
    app: AnyApplication = (
        Application.builder().token(settings.telegram_bot_token.get_secret_value()).build()
    )

    app.bot_data[_BOT_DATA_MODEL] = model_name
    app.bot_data[_BOT_DATA_CLIENT] = hevy_client
    app.bot_data[_BOT_DATA_SETTINGS] = settings
    app.bot_data[_BOT_DATA_STATE] = state

    # Restrict every handler to the single authorised chat ID
    owner = filters.Chat(chat_id=settings.telegram_chat_id)

    app.add_handler(CommandHandler("start", _handle_start, filters=owner))
    app.add_handler(CommandHandler("analyze", _handle_analyze, filters=owner))
    app.add_handler(CommandHandler("status", _handle_status, filters=owner))
    app.add_handler(CommandHandler("clear", _handle_clear, filters=owner))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & owner, _handle_message))

    # Catch-all: log and silently drop anything from unauthorised senders
    app.add_handler(MessageHandler(~owner, _handle_unauthorized))

    logger.info(
        "Telegram bot built — all handlers restricted to chat_id={}",
        settings.telegram_chat_id,
    )
    return app


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------


async def _handle_unauthorized(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Silently drop messages from anyone who is not the owner."""
    chat = update.effective_chat
    user = update.effective_user
    logger.warning(
        "Unauthorized access attempt -- chat_id={} user_id={} username={!r}",
        chat.id if chat else "?",
        user.id if user else "?",
        user.username if user else "?",
    )
    # No reply — don't reveal the bot exists to strangers


async def _handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    user = update.effective_user
    logger.info("Command /start from user={}", user.id if user else "unknown")
    await update.message.reply_text(
        "PumpPal online.\n\n"
        "I'll message you automatically after each Hevy workout.\n"
        "Commands:\n"
        "  /analyze -- analyse your latest workout now\n"
        "  /status  -- show current state\n"
        "  /clear   -- reset the current session"
    )


async def _handle_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    state = cast(ConversationState, context.bot_data[_BOT_DATA_STATE])
    pw = state.pending_workout
    detail = f" ({pw.workout_title})" if pw else ""
    logger.info("Command /status -- phase={}{}", state.phase.name, detail)
    await update.message.reply_text(f"Phase: {state.phase.name}{detail}")


async def _handle_clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    state = cast(ConversationState, context.bot_data[_BOT_DATA_STATE])
    old_phase = state.phase
    async with state.lock:
        state.clear()
    logger.info("Command /clear -- phase {} -> IDLE, history cleared", old_phase.name)
    await update.message.reply_text("Session cleared. Ready for the next workout.")


async def _handle_analyze(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show last 10 workouts and ask the user which one to analyse."""
    if update.message is None:
        return
    state = cast(ConversationState, context.bot_data[_BOT_DATA_STATE])
    hevy_client = cast(HevyClient, context.bot_data[_BOT_DATA_CLIENT])
    logger.info("Command /analyze -- current phase={}", state.phase.name)

    async with state.lock:
        if state.phase not in (ConversationPhase.IDLE, ConversationPhase.CHATTING):
            logger.warning("Command /analyze rejected -- phase={} (busy)", state.phase.name)
            await update.message.reply_text(
                "Already busy with a session. Use /clear to reset first."
            )
            return

        page = await hevy_client.get_recent_workouts(page_size=10)
        if not page.workouts:
            logger.warning("Command /analyze -- no workouts found in Hevy")
            await update.message.reply_text("No workouts found in Hevy.")
            return

        options = [
            PendingWorkout(
                workout_id=w.id,
                workout_title=w.title,
                num_exercises=len(w.exercises),
                total_sets=w.total_sets,
                duration_minutes=w.duration_minutes,
                workout_date=w.start_time.date(),
            )
            for w in page.workouts
        ]
        state.workout_options = options
        state.phase = ConversationPhase.AWAITING_WORKOUT_SELECTION

    logger.info(
        "Command /analyze -- listing {} workouts for selection",
        len(options),
    )
    lines = ["Which workout to analyse? Reply with a number:\n"]
    for i, pw in enumerate(options, 1):
        lines.append(
            f"{i}. {pw.workout_date} — *{pw.workout_title}* "
            f"({pw.num_exercises} ex, {pw.total_sets} sets, {pw.duration_minutes} min)"
        )
    await update.message.reply_markdown("\n".join(lines))


# ---------------------------------------------------------------------------
# Message handler -- routes by phase
# ---------------------------------------------------------------------------


async def _handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.message.text is None:
        return

    state = cast(ConversationState, context.bot_data[_BOT_DATA_STATE])
    text = update.message.text.strip()
    phase = state.phase

    logger.debug(
        "Message received -- phase={} text={!r}",
        phase.name,
        text[:60],
    )

    if phase == ConversationPhase.AWAITING_WORKOUT_SELECTION:
        await _do_workout_selection(update, context, text)
    elif phase == ConversationPhase.AWAITING_USER_NOTES:
        await _do_analysis(update, context, text)
    elif phase == ConversationPhase.CHATTING:
        await _do_followup(update, context, text)
    elif phase == ConversationPhase.AGENT_RUNNING:
        logger.debug("Message ignored -- agent already running")
        await update.message.reply_text("Thinking... please wait.")
    else:
        logger.debug("Message ignored -- phase=IDLE, no active session")
        await update.message.reply_text(
            "No active session. Use /analyze or wait for your next workout."
        )


async def _do_workout_selection(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_text: str,
) -> None:
    assert update.message is not None
    state = cast(ConversationState, context.bot_data[_BOT_DATA_STATE])

    try:
        n = int(user_text.strip())
    except ValueError:
        await update.message.reply_text("Please reply with a number to pick a workout.")
        return

    async with state.lock:
        options = state.workout_options
        if not options:
            logger.error("_do_workout_selection: no workout_options in state -- resetting")
            state.phase = ConversationPhase.IDLE
            await update.message.reply_text("Session lost. Use /analyze to start again.")
            return
        if not 1 <= n <= len(options):
            await update.message.reply_text(f"Please pick a number between 1 and {len(options)}.")
            return

        pw = options[n - 1]
        state.pending_workout = pw
        state.workout_options = []
        state.reset_session()
        state.phase = ConversationPhase.AWAITING_USER_NOTES

    logger.info(
        "Workout selected -- n={} workout={!r} id={} date={} phase->AWAITING_USER_NOTES",
        n,
        pw.workout_title,
        pw.workout_id,
        pw.workout_date,
    )
    await update.message.reply_markdown(
        f"Got it — analysing *{pw.workout_title}* "
        f"({pw.num_exercises} exercises, {pw.total_sets} sets, "
        f"{pw.duration_minutes} min).\n\nAnything to add before I start?"
    )


async def _do_analysis(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_text: str,
) -> None:
    assert update.message is not None
    state = cast(ConversationState, context.bot_data[_BOT_DATA_STATE])
    hevy_client = cast(HevyClient, context.bot_data[_BOT_DATA_CLIENT])
    settings = cast(Settings, context.bot_data[_BOT_DATA_SETTINGS])
    model_name = cast(str, context.bot_data[_BOT_DATA_MODEL])

    async with state.lock:
        if user_text.lower() in {"go", "go ahead", "nope", "no", "n", "start"}:
            state.user_notes = ""
        else:
            state.user_notes = user_text
        state.phase = ConversationPhase.AGENT_RUNNING
        pw = state.pending_workout
        history = list(state.message_history)
        notes = state.user_notes

    logger.info(
        "Phase AWAITING_USER_NOTES -> AGENT_RUNNING -- notes={!r}",
        notes[:80] if notes else "(none)",
    )

    if pw is None:
        logger.error("_do_analysis called but pending_workout is None -- resetting")
        await update.message.reply_text("No pending workout. Use /analyze.")
        async with state.lock:
            state.phase = ConversationPhase.IDLE
        return

    await update.message.reply_text("On it! Analysing your workout...")

    deps = AgentDeps(
        hevy_client=hevy_client,
        settings=settings,
        workout_id=pw.workout_id,
        workout_date=pw.workout_date,
        user_notes=notes,
        kb_dir=settings.kb_dir,
        coach_log_path=settings.coach_log_path,
    )

    try:
        response, new_history = await run_analysis(model_name, deps, history)
    except Exception:
        logger.exception("Agent error during analysis for workout_id={}", pw.workout_id)
        async with state.lock:
            state.phase = ConversationPhase.IDLE
        await update.message.reply_text("Something went wrong during analysis. Check logs.")
        return

    async with state.lock:
        state.message_history = new_history
        state.phase = ConversationPhase.CHATTING

    logger.info(
        "Phase AGENT_RUNNING -> CHATTING -- response={} chars",
        len(response),
    )
    await _send_long_message(update, response)


async def _do_followup(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_text: str,
) -> None:
    assert update.message is not None
    state = cast(ConversationState, context.bot_data[_BOT_DATA_STATE])
    hevy_client = cast(HevyClient, context.bot_data[_BOT_DATA_CLIENT])
    settings = cast(Settings, context.bot_data[_BOT_DATA_SETTINGS])
    model_name = cast(str, context.bot_data[_BOT_DATA_MODEL])

    async with state.lock:
        state.phase = ConversationPhase.AGENT_RUNNING
        pw = state.pending_workout
        history = list(state.message_history)
        notes = state.user_notes

    logger.info(
        "Phase CHATTING -> AGENT_RUNNING -- follow-up={!r} history={} msgs",
        user_text[:60],
        len(history),
    )

    if pw is None:
        logger.error("_do_followup called but pending_workout is None -- resetting")
        await update.message.reply_text("Session lost. Use /analyze to start again.")
        async with state.lock:
            state.phase = ConversationPhase.IDLE
        return

    deps = AgentDeps(
        hevy_client=hevy_client,
        settings=settings,
        workout_id=pw.workout_id,
        workout_date=pw.workout_date,
        user_notes=notes,
        kb_dir=settings.kb_dir,
        coach_log_path=settings.coach_log_path,
    )

    try:
        response, new_history = await run_followup(model_name, deps, user_text, history)
    except Exception:
        logger.exception("Agent error during follow-up for workout_id={}", pw.workout_id)
        async with state.lock:
            state.phase = ConversationPhase.CHATTING  # recover gracefully
        await update.message.reply_text("Something went wrong. Please try again.")
        return

    async with state.lock:
        state.message_history = new_history
        state.phase = ConversationPhase.CHATTING

    logger.info(
        "Phase AGENT_RUNNING -> CHATTING -- response={} chars, history now {} msgs",
        len(response),
        len(new_history),
    )
    await _send_long_message(update, response)


async def _send_long_message(update: Update, text: str) -> None:
    """Split and send messages exceeding Telegram's 4096-char limit."""
    assert update.message is not None
    max_len = 4000
    if len(text) <= max_len:
        await update.message.reply_markdown(text)
        return
    chunks: list[str] = []
    current = ""
    for paragraph in text.split("\n\n"):
        if len(current) + len(paragraph) + 2 > max_len:
            if current:
                chunks.append(current.strip())
            current = paragraph
        else:
            current = f"{current}\n\n{paragraph}" if current else paragraph
    if current:
        chunks.append(current.strip())
    logger.debug("Splitting long message into {} chunks ({} chars)", len(chunks), len(text))
    for chunk in chunks:
        await update.message.reply_markdown(chunk)
