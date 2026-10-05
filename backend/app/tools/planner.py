"""Academic Planner — a real study-planning engine over syllabus data.

Plans are built from the structured syllabus records (subjects, units,
exam dates) plus tracked progress:

  plan_week / plan_semester  day-by-day study plan. One session = one unit
                             (split into parts when a unit exceeds the daily
                             budget), 45-90 min per session, a buffer is
                             reserved on longer days, and a day never exceeds
                             the available hours (default 2h/day, stated as an
                             assumption when the user hasn't said otherwise).
  today                      what to study today (from the stored plan, or a
                             fresh week plan when none exists) + the priority
                             reasoning.
  mark_complete              mark a subject's unit done: progress updates and
                             the future plan is rebuilt from the remaining
                             units (no duplicates, no stale sessions).
  missed_today               move today's unfinished sessions to the next
                             days with free capacity; reports what changed.
  progress                   per-subject real percentages from completed /
                             total units; honest when there is no data.
  priority                   explains its reasoning ("DBMS is today's highest
                             priority because its exam is closest and Unit 3
                             remains incomplete").
  countdown                  days remaining per known exam date.
  start_session              log a study session start (used by the
                             "start a DBMS study session" flow).

Plan and progress are stored via MemoryTool (``[study-plan]`` /
``[syllabus-progress]`` namespaces), append-only with newest-wins, so they
persist, are searchable, and are forgettable.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta

from app.tools.base import BaseTool, ToolError
from app.tools.memory_tool import MemoryTool
from app.tools.syllabus import all_subjects, find_subject, load_documents

_mem = MemoryTool()

_PLAN_MARKER = "[study-plan]"
_PROGRESS_MARKER = "[syllabus-progress]"

_DEFAULT_HOURS_PER_DAY = 2.0
_BUFFER_MINUTES = 15        # reserved on days with >= 90 min budget
_MAX_SESSIONS_PER_DAY = 4


def _utcnow_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _session_minutes(unit: dict) -> int:
    """45-90 min per unit, scaled by topic count (real data, not invented)."""
    n = len(unit.get("topics", []))
    if n <= 3:
        return 45
    if n <= 8:
        return 60
    return 90


def _usable_minutes(budget: int) -> int:
    return budget - _BUFFER_MINUTES if budget >= 90 else budget


# ---------------------------------------------------------------------------
# Stores
# ---------------------------------------------------------------------------

async def _load_plan() -> dict | None:
    res = await _mem.execute(action="recall", query="study-plan", limit=10)
    cands = []
    for m in res.get("results", []):
        content = m.get("content", "")
        if content.startswith(_PLAN_MARKER):
            try:
                cands.append(json.loads(content.split("::", 1)[1]))
            except (json.JSONDecodeError, IndexError):
                continue
    # Memory is append-only and recall returns oldest-first: the newest
    # saved plan wins.
    return cands[-1] if cands else None


async def _save_plan(plan: dict) -> None:
    await _mem.execute(
        action="save",
        content=f"{_PLAN_MARKER} plan :: " + json.dumps(plan, ensure_ascii=False),
        category="study", importance=0.7,
    )


async def _load_progress() -> dict:
    res = await _mem.execute(action="recall", query="syllabus-progress", limit=10)
    cands = []
    for m in res.get("results", []):
        content = m.get("content", "")
        if content.startswith(_PROGRESS_MARKER):
            try:
                cands.append(json.loads(content.split("::", 1)[1]))
            except (json.JSONDecodeError, IndexError):
                continue
    # Newest saved progress wins (memory is append-only, oldest-first).
    return cands[-1] if cands else {"subjects": {}}


async def _save_progress(progress: dict) -> None:
    progress["updated"] = _utcnow_iso()
    await _mem.execute(
        action="save",
        content=f"{_PROGRESS_MARKER} progress :: " + json.dumps(progress, ensure_ascii=False),
        category="study", importance=0.7,
    )


def _subject_key(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip().lower())


def _completed_units(progress: dict, key: str) -> set[str]:
    return set((progress.get("subjects", {}).get(key) or {}).get("completed", []))


def _unit_num(u: dict) -> int:
    try:
        return int(str(u.get("number", "0")))
    except ValueError:
        return 999


# ---------------------------------------------------------------------------
# Scheduling core
# ---------------------------------------------------------------------------

async def _exam_dates_by_subject() -> dict[str, dict]:
    """Nearest upcoming exam per subject key: {key: {date, days_left, type}}."""
    from app.tools.syllabus import SyllabusTool  # local: avoids import cycle
    try:
        out = await SyllabusTool().execute(action="exams")
    except ToolError:
        return {}
    by_key: dict[str, dict] = {}
    for e in out.get("exams", []):
        key = _subject_key(e.get("subject") or "")
        if not key or key in by_key:
            continue
        if (e.get("days_left") or 0) >= 0:
            by_key[key] = e
    return by_key


def _order_subjects(subjects: list[dict], exams: dict[str, dict]) -> list[dict]:
    def rank(s: dict) -> tuple:
        e = exams.get(_subject_key(s.get("name", "")))
        days = e["days_left"] if e and e.get("days_left") is not None else 9999
        return (days, s.get("name", ""))
    return sorted(subjects, key=rank)


def _remaining_work(subjects: list[dict], progress: dict) -> list[dict]:
    """Incomplete units across subjects, each as a work item."""
    items = []
    for s in subjects:
        key = _subject_key(s.get("name", ""))
        done = _completed_units(progress, key)
        for u in sorted(s.get("units", []), key=_unit_num):
            num = str(u.get("number", ""))
            if num in done:
                continue
            items.append({
                "subject": s.get("name", ""), "subject_key": key,
                "code": s.get("code"), "unit": num,
                "unit_title": u.get("title", ""), "topics": u.get("topics", []),
                "minutes": _session_minutes(u),
                "source": s.get("_source"),
            })
    return items


def _schedule(
    items: list[dict], start: date, days: int, budget: int,
    prefilled: dict[str, list[dict]] | None = None,
) -> tuple[list[dict], list[dict]]:
    """Greedy day-fill: sessions never exceed the daily budget; a buffer is
    reserved on longer days. Returns (scheduled, unscheduled)."""
    usable = _usable_minutes(budget)
    day_load: dict[str, int] = {}
    day_count: dict[str, int] = {}
    if prefilled:
        for d, sess in prefilled.items():
            day_load[d] = sum(s.get("minutes", 0) for s in sess)
            day_count[d] = len(sess)
    scheduled: list[dict] = []
    unscheduled: list[dict] = []
    queue = list(items)
    for offset in range(days):
        if not queue:
            break
        day = (start + timedelta(days=offset)).isoformat()
        used = day_load.get(day, 0)
        count = day_count.get(day, 0)
        while queue and count < _MAX_SESSIONS_PER_DAY:
            item = queue[0]
            mins = item["minutes"]
            # Split oversized units into parts so no day exceeds its budget.
            if mins > usable:
                parts = (mins + usable - 1) // usable
                per = (mins + parts - 1) // parts
                first, rest = dict(item), dict(item)
                first["minutes"] = per
                first["part"] = f"1 of {parts}"
                rest["minutes"] = mins - per
                rest["part"] = f"2 of {parts}" if parts == 2 else f"rest of {parts}"
                item = first
                queue[0] = rest
                mins = per
            if used + mins > usable:
                break
            queue.pop(0)
            used += mins
            count += 1
            scheduled.append({
                "id": f"{day}-{item['subject_key']}-u{item['unit']}-{count}",
                "date": day, "subject": item["subject"],
                "subject_key": item["subject_key"], "code": item.get("code"),
                "unit": item["unit"], "unit_title": item.get("unit_title", ""),
                "part": item.get("part"), "minutes": mins,
                "status": "pending",
            })
        day_load[day] = used
    unscheduled = queue
    return scheduled, unscheduled


async def _build_plan(
    hours: float | None, start: date, days: int,
) -> tuple[dict, bool]:
    """Build (and store) a plan. Returns (plan_dict, hours_were_assumed)."""
    docs = await load_documents()
    subjects = [s for s in all_subjects(docs) if s.get("units")]
    if not subjects:
        raise ToolError(
            "No syllabus data yet — ingest a syllabus document first "
            "(attach the PDF and say 'this is my syllabus')."
        )
    assumed = hours is None
    budget = int(round((hours if hours is not None else _DEFAULT_HOURS_PER_DAY) * 60))
    if budget < 30:
        raise ToolError("Give me at least 30 minutes a day to plan with.")
    progress = await _load_progress()
    exams = await _exam_dates_by_subject()
    ordered = _order_subjects(subjects, exams)
    items = _remaining_work(ordered, progress)
    scheduled, unscheduled = _schedule(items, start, days, budget)
    plan = {
        "budget_per_day": budget,
        "hours_per_day": budget / 60,
        "hours_assumed": assumed,
        "start": start.isoformat(),
        "days": days,
        "created": _utcnow_iso(),
        "sessions": scheduled,
        "unscheduled": [
            {"subject": i["subject"], "unit": i["unit"],
             "unit_title": i.get("unit_title", ""), "minutes": i["minutes"]}
            for i in unscheduled
        ],
    }
    await _save_plan(plan)
    return plan, assumed


def _plan_summary(plan: dict) -> dict:
    by_day: dict[str, list[dict]] = {}
    for s in plan.get("sessions", []):
        by_day.setdefault(s["date"], []).append(s)
    days = []
    for d in sorted(by_day):
        sess = by_day[d]
        days.append({
            "date": d,
            "total_minutes": sum(s["minutes"] for s in sess),
            "sessions": [
                {"subject": s["subject"], "unit": s["unit"],
                 "unit_title": s.get("unit_title", ""), "part": s.get("part"),
                 "minutes": s["minutes"], "status": s["status"]}
                for s in sess
            ],
        })
    return {
        "hours_per_day": plan.get("hours_per_day"),
        "hours_assumed": plan.get("hours_assumed", False),
        "days": days,
        "unscheduled": plan.get("unscheduled", []),
        "created": plan.get("created"),
    }


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------

class PlannerTool(BaseTool):
    name = "planner"
    description = (
        "Academic study planner: day-by-day plans from syllabus units and "
        "exam dates, progress tracking, priorities with reasoning, exam "
        "countdowns, and missed-day rebalancing."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "plan_week", "plan_semester", "today", "mark_complete",
                    "missed_today", "progress", "priority", "countdown",
                    "start_session", "next_unit",
                ],
            },
            "available_hours_per_day": {"type": "number"},
            "start_date": {"type": "string", "description": "YYYY-MM-DD"},
            "weeks": {"type": "integer"},
            "subject": {"type": "string"},
            "unit": {"type": "string"},
        },
        "required": ["action"],
    }
    output_schema = {"result": "object"}
    permission = "read"
    action_permissions = {
        "plan_week": "low_write",
        "plan_semester": "low_write",
        "mark_complete": "low_write",
        "missed_today": "low_write",
        "start_session": "low_write",
    }

    async def _run(self, **kwargs) -> dict:
        action = (kwargs.get("action") or "").strip().lower()
        if action == "plan_week":
            return {"plan": await self._plan_week(kwargs)}
        if action == "plan_semester":
            return {"plan": await self._plan_semester(kwargs)}
        if action == "today":
            return {"today": await self._today()}
        if action == "mark_complete":
            return {"updated": await self._mark_complete(kwargs)}
        if action == "missed_today":
            return {"moved": await self._missed_today()}
        if action == "progress":
            return {"progress": await self._progress()}
        if action == "priority":
            return {"priority": await self._priority()}
        if action == "countdown":
            return {"countdown": await self._countdown()}
        if action == "start_session":
            return {"session": await self._start_session(kwargs)}
        if action == "next_unit":
            return {"next_unit": await self._next_unit(kwargs)}
        raise ToolError(f"Unknown planner action: {action!r}.")

    # -- plans ------------------------------------------------------------
    @staticmethod
    def _parse_start(kwargs: dict) -> date:
        raw = (kwargs.get("start_date") or "").strip()
        if not raw:
            return date.today()
        try:
            return date.fromisoformat(raw)
        except ValueError:
            raise ToolError(f"start_date must be YYYY-MM-DD, got {raw!r}.")

    async def _plan_week(self, kwargs: dict) -> dict:
        hours = kwargs.get("available_hours_per_day")
        hours = float(hours) if hours is not None else None
        plan, _ = await _build_plan(hours, self._parse_start(kwargs), 7)
        return _plan_summary(plan)

    async def _plan_semester(self, kwargs: dict) -> dict:
        hours = kwargs.get("available_hours_per_day")
        hours = float(hours) if hours is not None else None
        weeks = kwargs.get("weeks")
        if weeks is None:
            # Default horizon: last known exam date, else 12 weeks.
            exams = await _exam_dates_by_subject()
            latest = None
            for e in exams.values():
                d = e.get("date")
                if d and (latest is None or d > latest):
                    latest = d
            if latest:
                try:
                    delta = (date.fromisoformat(latest) - date.today()).days
                    weeks = max(1, min(24, (delta + 6) // 7 + 1))
                except ValueError:
                    weeks = 12
            else:
                weeks = 12
        weeks = max(1, min(int(weeks), 24))
        plan, _ = await _build_plan(hours, self._parse_start(kwargs), weeks * 7)
        return _plan_summary(plan)

    async def _today(self) -> dict:
        plan = await _load_plan()
        assumed_note = None
        if not plan or not plan.get("sessions"):
            plan, assumed = await _build_plan(None, date.today(), 7)
            if assumed:
                assumed_note = (
                    "I assumed 2 hours/day — tell me your real daily "
                    "availability and I'll replan."
                )
        today = date.today().isoformat()
        sessions = [s for s in plan["sessions"]
                    if s["date"] == today and s["status"] == "pending"]
        prio = await self._priority()
        return {
            "date": today,
            "sessions": [
                {"subject": s["subject"], "unit": s["unit"],
                 "unit_title": s.get("unit_title", ""), "part": s.get("part"),
                 "minutes": s["minutes"]}
                for s in sessions
            ],
            "priority": prio,
            "assumption": assumed_note,
        }

    # -- progress ---------------------------------------------------------
    async def _resolve(self, subject_q: str, unit_q: str | None = None) -> tuple[dict, dict | None]:
        docs = await load_documents()
        subjects = [s for s in all_subjects(docs) if s.get("units")]
        if not subjects:
            raise ToolError("No syllabus data yet — ingest a syllabus first.")
        hit = find_subject(subject_q or "", subjects)
        if not hit:
            names = ", ".join(f"\"{s['name']}\"" for s in subjects)
            raise ToolError(
                f"I couldn't find a subject matching {subject_q!r}. "
                f"Your subjects: {names}."
            )
        unit = None
        if unit_q:
            unit = self._find_unit(hit, unit_q)
        return hit, unit

    @staticmethod
    def _find_unit(subject: dict, unit_q: str) -> dict:
        units = subject.get("units", [])
        q = unit_q.strip().lower()
        m = re.search(r"(\d+)", q)
        if m:
            num = m.group(1).lstrip("0") or "0"
            for u in units:
                if (str(u.get("number", "")).lstrip("0") or "0") == num:
                    return u
        for u in units:
            if q in str(u.get("title", "")).lower():
                return u
        valid = ", ".join(f"Unit {u.get('number')}" for u in units)
        raise ToolError(
            f"Unit {unit_q!r} isn't in {subject.get('name')}. "
            f"Available: {valid or 'none'}."
        )

    async def _mark_complete(self, kwargs: dict) -> dict:
        subject_q = (kwargs.get("subject") or "").strip()
        unit_q = (kwargs.get("unit") or "").strip()
        if not subject_q:
            raise ToolError("Which subject? Say e.g. 'mark DBMS Unit 2 complete'.")
        if not unit_q:
            raise ToolError("Which unit? Say e.g. 'mark DBMS Unit 2 complete'.")
        subject, unit = await self._resolve(subject_q, unit_q)
        key = _subject_key(subject["name"])
        num = str(unit.get("number", ""))
        progress = await _load_progress()
        entry = progress.setdefault("subjects", {}).setdefault(
            key, {"name": subject["name"], "completed": [], "sessions": 0})
        entry["name"] = subject["name"]
        if num not in entry["completed"]:
            entry["completed"].append(num)
        await _save_progress(progress)

        # Rebalance: mark matching sessions done, drop future pending
        # sessions, rebuild from the remaining units (no duplicates).
        plan = await _load_plan()
        rebuilt = None
        if plan:
            today = date.today().isoformat()
            kept: list[dict] = []
            for s in plan.get("sessions", []):
                if s.get("subject_key") == key and str(s.get("unit")) == num:
                    s["status"] = "done"
                kept.append(s)
            prefilled: dict[str, list[dict]] = {}
            for s in kept:
                if s["date"] >= today and s["status"] == "pending":
                    continue  # dropped: will be rebuilt
                prefilled.setdefault(s["date"], []).append(s)
            docs = await load_documents()
            subjects = [s for s in all_subjects(docs) if s.get("units")]
            exams = await _exam_dates_by_subject()
            items = _remaining_work(_order_subjects(subjects, exams), progress)
            budget = plan.get("budget_per_day") or 120
            scheduled, unscheduled = _schedule(items, date.today(), 14, budget, prefilled)
            # Reuse ids' uniqueness: rebuilt sessions get fresh ids.
            plan["sessions"] = [s for s in kept if not (s["date"] >= today and s["status"] == "pending")] + scheduled
            plan["unscheduled"] = [
                {"subject": i["subject"], "unit": i["unit"],
                 "unit_title": i.get("unit_title", ""), "minutes": i["minutes"]}
                for i in unscheduled
            ]
            await _save_plan(plan)
            rebuilt = _plan_summary(plan)
        return {
            "subject": subject["name"],
            "unit": num,
            "unit_title": unit.get("title", ""),
            "progress": self._subject_progress(subject, progress),
            "plan": rebuilt,
        }

    @staticmethod
    def _subject_progress(subject: dict, progress: dict) -> dict:
        key = _subject_key(subject.get("name", ""))
        done = _completed_units(progress, key)
        total = len(subject.get("units", []))
        return {
            "subject": subject.get("name"),
            "completed": len(done),
            "total": total,
            "percent": round(100 * len(done) / total) if total else 0,
        }

    async def _progress(self) -> dict:
        docs = await load_documents()
        subjects = [s for s in all_subjects(docs) if s.get("units")]
        if not subjects:
            return {
                "subjects": [],
                "message": "No syllabus data yet — ingest a syllabus document first.",
            }
        progress = await _load_progress()
        rows = [self._subject_progress(s, progress) for s in subjects]
        for r, s in zip(rows, subjects):
            r["source"] = s.get("_source")
        total_done = sum(r["completed"] for r in rows)
        total_units = sum(r["total"] for r in rows)
        return {
            "subjects": rows,
            "overall": {
                "completed": total_done, "total": total_units,
                "percent": round(100 * total_done / total_units) if total_units else 0,
            },
        }

    # -- missed day -------------------------------------------------------
    async def _missed_today(self) -> dict:
        plan = await _load_plan()
        if not plan or not plan.get("sessions"):
            raise ToolError("No study plan yet — say 'plan my week' first.")
        today = date.today().isoformat()
        budget = plan.get("budget_per_day") or 120
        usable = _usable_minutes(budget)
        pending_today = [s for s in plan["sessions"]
                         if s["date"] == today and s["status"] == "pending"]
        if not pending_today:
            return {"moved": [], "date": today,
                    "message": "Nothing unfinished today — nice work."}
        day_load: dict[str, int] = {}
        for s in plan["sessions"]:
            if s["status"] == "pending" and s["date"] != today:
                day_load[s["date"]] = day_load.get(s["date"], 0) + s["minutes"]
        moved: list[dict] = []
        unmoved: list[dict] = []
        for s in pending_today:
            placed = False
            for offset in range(1, 61):
                day = (date.today() + timedelta(days=offset)).isoformat()
                if day_load.get(day, 0) + s["minutes"] <= usable:
                    moved.append({
                        "subject": s["subject"], "unit": s["unit"],
                        "unit_title": s.get("unit_title", ""),
                        "from": today, "to": day,
                    })
                    s["date"] = day
                    # Keep the id stable (no duplicate sessions created).
                    day_load[day] = day_load.get(day, 0) + s["minutes"]
                    placed = True
                    break
            if not placed:
                unmoved.append({"subject": s["subject"], "unit": s["unit"]})
        await _save_plan(plan)
        out: dict = {"moved": moved, "date": today}
        if unmoved:
            out["could_not_fit"] = unmoved
            out["message"] = (
                f"Moved {len(moved)} session(s) forward; "
                f"{len(unmoved)} wouldn't fit in the next 60 days — "
                "consider more hours per day."
            )
        else:
            out["message"] = (
                f"Moved {len(moved)} unfinished session(s) to upcoming days."
            )
        return out

    # -- priority / countdown ---------------------------------------------
    async def _priority(self) -> dict:
        docs = await load_documents()
        subjects = [s for s in all_subjects(docs) if s.get("units")]
        if not subjects:
            return {"subject": None,
                    "reasoning": "No syllabus data yet — ingest a syllabus first."}
        progress = await _load_progress()
        exams = await _exam_dates_by_subject()
        cands = []
        for s in subjects:
            key = _subject_key(s.get("name", ""))
            done = _completed_units(progress, key)
            pending = [u for u in sorted(s.get("units", []), key=_unit_num)
                       if str(u.get("number", "")) not in done]
            if not pending:
                continue
            e = exams.get(key)
            days = e["days_left"] if e and e.get("days_left") is not None else None
            cands.append((days if days is not None else 9999, -len(pending), s, pending[0], e))
        if not cands:
            return {"subject": None,
                    "reasoning": "Everything in your syllabus is marked complete — nothing left to prioritize."}
        cands.sort(key=lambda c: (c[0], c[1]))
        _, _, s, nxt, e = cands[0]
        total = len(s.get("units", []))
        left = len([u for u in s.get("units", [])
                    if str(u.get("number", "")) not in _completed_units(progress, _subject_key(s.get("name", "")))])
        if e and e.get("days_left") is not None:
            reasoning = (
                f"{s['name']} is today's highest priority because its exam "
                f"is closest (in {e['days_left']} days, on {e['date']}) and "
                f"Unit {nxt.get('number')} ('{nxt.get('title', '')}') remains incomplete."
            )
        else:
            reasoning = (
                f"{s['name']} is today's highest priority because it has the "
                f"most units remaining ({left} of {total}) and no exam date is "
                f"known yet — start with Unit {nxt.get('number')} ('{nxt.get('title', '')}')."
            )
        return {
            "subject": s["name"], "code": s.get("code"),
            "next_unit": {"number": nxt.get("number"), "title": nxt.get("title", "")},
            "exam": e, "source": s.get("_source"),
            "reasoning": reasoning,
        }

    async def _countdown(self) -> dict:
        from app.tools.syllabus import SyllabusTool  # local: avoids import cycle
        try:
            out = await SyllabusTool().execute(action="exams")
        except ToolError as exc:
            return {"exams": [], "message": exc.user_message}
        exams = out.get("exams", [])
        if not exams:
            return {"exams": [],
                    "message": "No exam dates known — ingest an exam timetable."}
        return {"exams": [
            {"subject": e.get("subject"), "code": e.get("code"),
             "date": e.get("date"), "type": e.get("type"),
             "days_left": e.get("days_left"), "source": e.get("source")}
            for e in exams
        ]}

    # -- study session ------------------------------------------------------
    async def _next_unit(self, kwargs: dict) -> dict:
        """Read-only peek at the next incomplete unit (no session logging)."""
        subject_q = (kwargs.get("subject") or "").strip()
        docs = await load_documents()
        subjects = [s for s in all_subjects(docs) if s.get("units")]
        if not subjects:
            raise ToolError("No syllabus data yet — ingest a syllabus first.")
        subject = find_subject(subject_q, subjects) if subject_q else None
        if subject is None:
            prio = await self._priority()
            if not prio.get("subject"):
                raise ToolError(
                    prio.get("reasoning", "No syllabus data yet."))
            subject = find_subject(prio["subject"], subjects)
            if subject is None:
                raise ToolError("No syllabus data yet.")
        progress = await _load_progress()
        key = _subject_key(subject["name"])
        done = _completed_units(progress, key)
        pending = [u for u in sorted(subject.get("units", []), key=_unit_num)
                   if str(u.get("number", "")) not in done]
        if not pending:
            raise ToolError(f"All units of {subject['name']} are complete.")
        u = pending[0]
        return {
            "subject": subject["name"], "number": str(u.get("number", "")),
            "title": u.get("title", ""), "topics": u.get("topics", []),
            "source": subject.get("_source"),
        }

    async def _start_session(self, kwargs: dict) -> dict:
        subject_q = (kwargs.get("subject") or "").strip()
        unit_q = (kwargs.get("unit") or "").strip()
        progress = await _load_progress()
        if not subject_q:
            prio = await self._priority()
            if not prio.get("subject"):
                raise ToolError(prio.get("reasoning", "No syllabus data yet."))
            subject_q = prio["subject"]
            unit_q = str(prio["next_unit"]["number"])
        subject, unit = await self._resolve(subject_q, unit_q or None)
        if unit is None:
            # Default to the next incomplete unit.
            key = _subject_key(subject["name"])
            done = _completed_units(progress, key)
            pending = [u for u in sorted(subject.get("units", []), key=_unit_num)
                       if str(u.get("number", "")) not in done]
            if not pending:
                raise ToolError(f"All units of {subject['name']} are complete.")
            unit = pending[0]
        key = _subject_key(subject["name"])
        entry = progress.setdefault("subjects", {}).setdefault(
            key, {"name": subject["name"], "completed": [], "sessions": 0})
        entry["sessions"] = int(entry.get("sessions", 0)) + 1
        await _save_progress(progress)
        return {
            "subject": subject["name"],
            "unit": str(unit.get("number", "")),
            "unit_title": unit.get("title", ""),
            "topics": unit.get("topics", []),
            "source": subject.get("_source"),
            "sessions_so_far": entry["sessions"],
        }
