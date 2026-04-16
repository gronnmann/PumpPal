from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from loguru import logger
from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import ModelMessage

from .hevy.client import HevyClient
from .hevy.models import RoutineModel, WorkoutModel
from .settings import Settings

# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


@dataclass
class AgentDeps:
    hevy_client: HevyClient
    settings: Settings
    workout_id: str
    workout_date: date
    user_notes: str
    kb_dir: Path
    coach_log_path: Path


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are PumpPal, a personal AI strength coach. You have access to the user's full \
workout history via Hevy, their current training routines, and a knowledge base (KB) \
of training methodology.

KNOWLEDGE BASE ORIENTATION (do this once per conversation if you need KB context):
- Call read_kb_index to read index.md -- it describes what the KB contains and \
  how to use it. Follow its instructions precisely.
- Call list_kb_files to see available files, then read_kb_file for specific content.

PHASE 1 -- ANALYSIS (runs when user sends notes or "go"):
1. Call read_coach_log -- recall past sessions, prior decisions, patterns.
2. Call get_current_workout -- fetch full exercise and set data.
3. Call get_recent_workouts -- identify trends, stalled lifts, PRs.
4. Call get_all_routines -- see the full program.
5. If the workout belongs to a routine, call get_routine for that routine.
6. Analyse performance: compare to previous sessions, flag regressions or PRs.
7. Determine what SHOULD change next session based on the KB methodology and \
   observed performance.
8. Write your coaching message (performance highlights, key observations, \
   relevant KB context).
9. List every proposed routine change explicitly, e.g.:
   - Bench Press: 60 kg -> 62.5 kg (hit 3x10, top of range)
   - Pull-up rest: 90 s -> 120 s (incomplete sets, need more recovery)
10. Ask the user: "Shall I apply these changes to your routine?" \
DO NOT call update_routine or append_coach_log yet.

PHASE 2 -- APPLYING CHANGES (runs after user confirms):
- If the user confirms (yes / apply / go / looks good / etc.): \
  call update_routine with the exact changes discussed, \
  then call append_coach_log to record the session.
- If the user says no or requests modifications: adjust the proposal and \
  ask again before applying.
- If there are no routine changes needed: call append_coach_log immediately \
  after the analysis message (no confirmation needed).

ONGOING CHAT:
After analysis you stay in a coaching conversation. The user may ask follow-up \
questions, request further changes, or discuss training in general. \
Answer fully and helpfully -- you are their personal coach, not just an analyser. \
Only call update_routine when the user explicitly asks for a change.

TONE: Direct, data-driven, encouraging. Use markdown (bold, bullet points). No fluff.
"""

# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

agent: Agent[AgentDeps, str] = Agent(
    # Model is None here; passed at run time via agent.run(model=...).
    # defer_model_check=True prevents connecting to the provider at import time
    # (before .env is loaded).
    None,
    deps_type=AgentDeps,
    instructions=_SYSTEM_PROMPT,
    defer_model_check=True,
)


def build_agent(settings: Settings) -> str:
    """Set OpenRouter credentials and return the model name for use at run time."""
    import os

    os.environ.setdefault(
        "OPENROUTER_API_KEY",
        settings.openrouter_api_key.get_secret_value(),
    )
    model_name = f"openrouter:{settings.openrouter_model}"
    logger.info("Agent configured with model={!r}", model_name)
    return model_name


# ---------------------------------------------------------------------------
# Tools -- Hevy
# ---------------------------------------------------------------------------


@agent.tool
async def get_current_workout(ctx: RunContext[AgentDeps]) -> WorkoutModel:
    """Fetch the full details of the just-completed workout."""
    logger.debug("Tool: get_current_workout (id={})", ctx.deps.workout_id)
    workout = await ctx.deps.hevy_client.get_workout(ctx.deps.workout_id)
    logger.debug(
        "Tool: get_current_workout done -- {} exercises, {} sets",
        len(workout.exercises),
        workout.total_sets,
    )
    return workout


@agent.tool
async def get_recent_workouts(
    ctx: RunContext[AgentDeps],
    page_size: int = 10,
) -> list[WorkoutModel]:
    """Fetch the N most recent workouts for trend analysis (max 10)."""
    page_size = min(page_size, 10)
    logger.debug("Tool: get_recent_workouts (page_size={})", page_size)
    page = await ctx.deps.hevy_client.get_recent_workouts(page_size=page_size)
    logger.debug(
        "Tool: get_recent_workouts done -- {} workouts returned",
        len(page.workouts),
    )
    return page.workouts


@agent.tool
async def get_all_routines(ctx: RunContext[AgentDeps]) -> list[RoutineModel]:
    """Fetch all routines (up to 5 pages)."""
    logger.debug("Tool: get_all_routines")
    results: list[RoutineModel] = []
    for page_num in range(1, 6):
        page = await ctx.deps.hevy_client.get_routines(page=page_num, page_size=10)
        results.extend(page.routines)
        if page_num >= page.page_count:
            break
    logger.debug("Tool: get_all_routines done -- {} routines total", len(results))
    return results


@agent.tool
async def get_routine(
    ctx: RunContext[AgentDeps],
    routine_id: str,
) -> RoutineModel:
    """Fetch a single routine by ID."""
    logger.debug("Tool: get_routine (id={})", routine_id)
    routine = await ctx.deps.hevy_client.get_routine(routine_id)
    logger.debug("Tool: get_routine done -- title={!r}", routine.title)
    return routine


@agent.tool
async def update_routine(
    ctx: RunContext[AgentDeps],
    routine_id: str,
    routine: RoutineModel,
) -> RoutineModel:
    """Apply progressive overload changes to a routine in Hevy."""
    logger.info(
        "Tool: update_routine (id={} title={!r})",
        routine_id,
        routine.title,
    )
    updated = await ctx.deps.hevy_client.update_routine(routine_id, routine)
    logger.info(
        "Tool: update_routine done -- routine {!r} persisted to Hevy",
        updated.title,
    )
    return updated


# ---------------------------------------------------------------------------
# Tools -- Knowledge base
# ---------------------------------------------------------------------------


@agent.tool
def read_kb_index(ctx: RunContext[AgentDeps]) -> str:
    """Read index.md from the knowledge base. Always call this first when you need
    KB context -- it describes what the KB contains and how to use it."""
    path = ctx.deps.kb_dir / "index.md"
    logger.debug("Tool: read_kb_index (path={})", path)
    if not path.exists():
        logger.warning("Tool: read_kb_index -- index.md not found in {}", ctx.deps.kb_dir)
        return "No index.md found in the knowledge base directory."
    content = path.read_text(encoding="utf-8")
    logger.debug("Tool: read_kb_index done -- {} chars", len(content))
    return content


@agent.tool
def list_kb_files(ctx: RunContext[AgentDeps]) -> list[str]:
    """List all markdown files available in the knowledge base (excluding index.md)."""
    path = ctx.deps.kb_dir
    if not path.exists():
        logger.warning("Tool: list_kb_files -- dir not found: {}", path)
        return []
    files = sorted(p.name for p in path.iterdir() if p.suffix == ".md" and p.name != "index.md")
    logger.debug("Tool: list_kb_files -- {} files found", len(files))
    return files


@agent.tool
def read_kb_file(
    ctx: RunContext[AgentDeps],
    filename: str,
) -> str:
    """Read a knowledge base file by filename. Returns full markdown text."""
    path = ctx.deps.kb_dir / filename
    logger.debug("Tool: read_kb_file (file={})", filename)
    if not path.exists():
        available = list_kb_files(ctx)
        logger.warning(
            "Tool: read_kb_file -- file not found: {}. Available: {}",
            filename,
            available,
        )
        return f"File '{filename}' not found. Available: {available}"
    content = path.read_text(encoding="utf-8")
    logger.debug(
        "Tool: read_kb_file done -- {} chars from {}",
        len(content),
        filename,
    )
    return content


# ---------------------------------------------------------------------------
# Tools -- Coach log (persistent memory)
# ---------------------------------------------------------------------------


@agent.tool
def read_coach_log(ctx: RunContext[AgentDeps]) -> str:
    """Read the full coach log (doctor's journal of past sessions).
    Always call this first at the start of every analysis."""
    path = ctx.deps.coach_log_path
    logger.debug("Tool: read_coach_log (path={})", path)
    if not path.exists():
        logger.info("Tool: read_coach_log -- no log file yet")
        return "No previous sessions recorded yet."
    content = path.read_text(encoding="utf-8")
    entry_count = content.count("\n## ")
    logger.info(
        "Tool: read_coach_log -- {} chars, ~{} past sessions loaded",
        len(content),
        entry_count,
    )
    return content


@agent.tool
def append_coach_log(ctx: RunContext[AgentDeps], entry: str) -> str:
    """Append a dated session entry to the coach log.
    Always call this last, after analysis and routine updates are complete.

    Format the entry as markdown with sections:
    - Duration / sets / exercises
    - User notes
    - Performance highlights per exercise
    - Routine changes made
    - Coach observations / things to watch
    """
    path = ctx.deps.coach_log_path
    workout_date = ctx.deps.workout_date
    block = f"\n---\n\n## {workout_date.isoformat()} -- {ctx.deps.workout_id}\n\n{entry.strip()}\n"
    logger.debug(
        "Tool: append_coach_log -- writing {} chars to {} (workout_date={})",
        len(block),
        path,
        workout_date,
    )
    if not path.exists():
        path.write_text(block, encoding="utf-8")
    else:
        content = path.read_text(encoding="utf-8")
        content = _insert_coach_entry(content, block, workout_date)
        path.write_text(content, encoding="utf-8")
    logger.info("Tool: append_coach_log -- session recorded in {} (date={})", path, workout_date)
    return f"Coach log updated ({path})."


def _insert_coach_entry(content: str, block: str, workout_date: date) -> str:
    """Insert a log block at the correct chronological position."""
    pattern = re.compile(r"\n---\n\n## (\d{4}-\d{2}-\d{2})")
    for match in pattern.finditer(content):
        try:
            entry_date = date.fromisoformat(match.group(1))
        except ValueError:
            continue
        if entry_date > workout_date:
            pos = match.start()
            return content[:pos] + block + content[pos:]
    # All existing entries are on or before workout_date — append at end
    return content + block


# ---------------------------------------------------------------------------
# Run helpers
# ---------------------------------------------------------------------------


async def run_analysis(
    model_name: str,
    deps: AgentDeps,
    message_history: list[ModelMessage],
) -> tuple[str, list[ModelMessage]]:
    """Run the initial workout analysis. Returns (response, full_history)."""
    notes_part = f"\n\nUser notes: {deps.user_notes}" if deps.user_notes else ""
    prompt = f"Please analyse my latest workout (ID: {deps.workout_id}).{notes_part}"

    logger.info(
        "Agent: starting analysis -- workout_id={} has_notes={} model={}",
        deps.workout_id,
        bool(deps.user_notes),
        model_name,
    )
    result = await agent.run(
        prompt,
        model=model_name,
        deps=deps,
        message_history=message_history,
    )
    all_msgs = result.all_messages()
    logger.info(
        "Agent: analysis complete -- response={} chars, history={} messages",
        len(result.output),
        len(all_msgs),
    )
    return result.output, all_msgs


async def run_followup(
    model_name: str,
    deps: AgentDeps,
    user_message: str,
    message_history: list[ModelMessage],
) -> tuple[str, list[ModelMessage]]:
    """Continue the conversation with a follow-up message. Returns (response, full_history)."""
    logger.info(
        "Agent: follow-up -- message={!r} history={} messages",
        user_message[:80],
        len(message_history),
    )
    result = await agent.run(
        user_message,
        model=model_name,
        deps=deps,
        message_history=message_history,
    )
    all_msgs = result.all_messages()
    logger.info(
        "Agent: follow-up done -- response={} chars, history now {} messages",
        len(result.output),
        len(all_msgs),
    )
    return result.output, all_msgs
