# =========================
# agent.py (WITH TOOLS)
# =========================

import os
import socket
import asyncio
import logging
import time
import json
import httpx
from datetime import datetime, timedelta
from typing import Annotated
from collections import deque
from dotenv import load_dotenv

# -------------------------
# Force IPv4
# -------------------------
os.environ["AIOHTTP_RESOLVER"] = "threaded"
os.environ["NO_PROXY"] = "*"
socket.setdefaulttimeout(60)

# -------------------------
# Load environment variables
# -------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BASE_DIR)
# Load .env from project root first, then agent dir as fallback
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
load_dotenv(os.path.join(BASE_DIR, ".env"), override=False)

# -------------------------
# LiveKit imports
# -------------------------
from livekit.agents import (
    AutoSubscribe,
    JobContext,
    WorkerOptions,
    cli,
)
from livekit.agents.llm import ChatMessage, function_tool
from livekit.agents.voice import Agent, AgentSession
from livekit.agents.voice.room_io import RoomOptions

# -------------------------
# Plugin imports
# -------------------------
from livekit.plugins import deepgram, openai, cartesia
from supabase import create_client, Client

# -------------------------
# Logging
# -------------------------
logger = logging.getLogger("voice-agent")
logger.setLevel(logging.INFO)

# -------------------------
# Supabase Client
# -------------------------
supabase: Client = create_client(
    os.environ.get("SUPABASE_URL", ""),
    os.environ.get("SUPABASE_SERVICE_KEY", "")
)

# Debug URL on startup (Partial Log)
_supa_url = os.environ.get("SUPABASE_URL", "")
logger.info(f"🔌 Supabase URL Configured: {'Yes' if _supa_url else 'NO'} ({_supa_url[:15]}...)" )

# -------------------------
# In-memory storage (per-session)
# -------------------------
# Session data storage
class SessionData:
    def __init__(self):
        self.user_phone: str | None = None
        self.user_name: str | None = None
        self.appointments: list = []
        self.transcripts: list = []  # Brief state transcripts
        self.full_transcript: list = []  # Actual spoken text
        self.preferences: list = []
        self.session_start = datetime.now()
        self.usage = {
            "stt_seconds": 0.0,
            "tts_chars": 0,
            "llm_input_tokens": 0,
            "llm_output_tokens": 0
        }
        self.summary_sent = False
        self.llm_token_events = deque()
        
session_data = SessionData()

WORKDAY_START_HOUR = 10
WORKDAY_END_HOUR = 17
SLOT_LENGTH_MINUTES = 45
NO_CHANGE_WINDOW_MINUTES = 60
TPM_TARGET = 11800
TPM_REQUEST_HEADROOM = 900
TPM_WINDOW_SECONDS = 60

# Cost config (override via .env to match your real billing plan)
# STT: dollars per minute
STT_COST_PER_MINUTE = float(os.environ.get("COST_STT_PER_MINUTE_USD", "0.0058"))
# TTS: dollars per character
TTS_COST_PER_CHAR = float(os.environ.get("COST_TTS_PER_CHAR_USD", "0.00001"))
# LLM: dollars per token
LLM_INPUT_COST_PER_TOKEN = float(os.environ.get("COST_LLM_INPUT_PER_TOKEN_USD", "0.00000059"))
LLM_OUTPUT_COST_PER_TOKEN = float(os.environ.get("COST_LLM_OUTPUT_PER_TOKEN_USD", "0.00000079"))
LLM_PROVIDER = (os.environ.get("LLM_PROVIDER") or "groq").strip().lower()


def _build_llm():
    provider = (os.environ.get("LLM_PROVIDER") or "groq").strip().lower()
    if provider == "ollama":
        return openai.LLM(
            model=os.environ.get("OLLAMA_MODEL", "llama3.1:8b"),
            base_url=f"{os.environ.get('OLLAMA_URL', 'http://127.0.0.1:11434').rstrip('/')}/v1",
            api_key=os.environ.get("OLLAMA_API_KEY", "ollama"),
            timeout=60.0,
            temperature=0.2,
            max_completion_tokens=220,
            max_retries=2,
        )
    if provider == "cerebras":
        return openai.LLM(
            model=os.environ.get("CEREBRAS_MODEL", "llama3.1-8b"),
            base_url="https://api.cerebras.ai/v1",
            api_key=os.environ.get("CEREBRAS_API_KEY"),
            timeout=60.0,
            temperature=0.2,
            max_completion_tokens=220,
            max_retries=2,
        )
    return openai.LLM(
        model="llama-3.3-70b-versatile",
        base_url="https://api.groq.com/openai/v1",
        api_key=os.environ.get("GROQ_API_KEY"),
        timeout=60.0,
        temperature=0.2,
        max_completion_tokens=220,
        max_retries=3,
    )

def get_cost_breakdown():

    stt_cost = (session_data.usage["stt_seconds"] / 60.0) * STT_COST_PER_MINUTE
    tts_cost = session_data.usage["tts_chars"] * TTS_COST_PER_CHAR
    llm_input_cost = session_data.usage["llm_input_tokens"] * LLM_INPUT_COST_PER_TOKEN
    llm_output_cost = session_data.usage["llm_output_tokens"] * LLM_OUTPUT_COST_PER_TOKEN
    llm_total_cost = llm_input_cost + llm_output_cost
    total = stt_cost + tts_cost + llm_total_cost
    
    return {
        "stt": {
            "usage": f"{session_data.usage['stt_seconds']:.1f}s", 
            "cost": stt_cost,
            "rate": f"${STT_COST_PER_MINUTE:.4f}/min"
        },
        "tts": {
            "usage": f"{session_data.usage['tts_chars']} chars", 
            "cost": tts_cost,
            "rate": f"${(TTS_COST_PER_CHAR*1_000_000):.2f}/1M chars"
        },
        "llm": {
            "usage": f"{session_data.usage['llm_input_tokens']}in + {session_data.usage['llm_output_tokens']}out tokens", 
            "cost": llm_total_cost,
            "rate": f"${(LLM_INPUT_COST_PER_TOKEN*1_000_000):.2f}/1M in, ${(LLM_OUTPUT_COST_PER_TOKEN*1_000_000):.2f}/1M out"
        },
        "total": total
    }


def _normalize_phone(phone_number: str) -> str:
    digits = "".join(ch for ch in phone_number if ch.isdigit())
    if len(digits) > 10:
        digits = digits[-10:]
    return digits

def _recent_user_text(limit: int = 4) -> str:
    user_lines = [t.get("text", "") for t in session_data.full_transcript if t.get("role") == "user"]
    return " ".join(user_lines[-limit:]).lower()

def _prune_llm_window(now_ts: float | None = None) -> None:
    now_ts = now_ts or time.time()
    cutoff = now_ts - TPM_WINDOW_SECONDS
    while session_data.llm_token_events and session_data.llm_token_events[0][0] < cutoff:
        session_data.llm_token_events.popleft()

def _llm_tokens_last_minute() -> int:
    _prune_llm_window()
    return sum(tokens for _, tokens in session_data.llm_token_events)

async def _throttle_for_tpm(expected_request_tokens: int = TPM_REQUEST_HEADROOM) -> None:
    if LLM_PROVIDER != "groq":
        return
    while True:
        used = _llm_tokens_last_minute()
        projected = used + expected_request_tokens
        if projected <= TPM_TARGET:
            return
        wait_s = 1.5
        if session_data.llm_token_events:
            oldest_ts = session_data.llm_token_events[0][0]
            wait_s = max(0.25, (oldest_ts + TPM_WINDOW_SECONDS) - time.time() + 0.1)
        logger.warning(f"⏳ TPM guard: used={used}, projected={projected}, target={TPM_TARGET}. Waiting {wait_s:.2f}s")
        await asyncio.sleep(wait_s)


def _words_to_digit_string(text: str) -> str:
    mapping = {
        "zero": "0", "oh": "0",
        "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
        "six": "6", "seven": "7", "eight": "8", "nine": "9",
    }
    out = []
    for token in (text or "").lower().replace("-", " ").split():
        if token in mapping:
            out.append(mapping[token])
    return "".join(out)


def _recent_user_turns(limit: int = 4) -> list[str]:
    return [t.get("text", "") for t in session_data.full_transcript if t.get("role") == "user"][-limit:]


def _extract_entities_from_text(text: str) -> dict:
    s = (text or "").lower()
    out = {"name": None, "phone": None, "date": None, "time": None, "intent": None}

    phone_match = None
    digits = "".join(ch for ch in s if ch.isdigit())
    if len(digits) >= 10:
        phone_match = digits[-10:]
    out["phone"] = phone_match

    if "today" in s:
        out["date"] = "today"
    elif "tomorrow" in s:
        out["date"] = "tomorrow"

    time_tokens = ["am", "pm", "noon", "midnight", ":"]
    if any(t in s for t in time_tokens):
        # lightweight extraction around "at"
        if " at " in s:
            out["time"] = s.split(" at ", 1)[1].split(" for ", 1)[0].strip()[:20]
        elif "noon" in s:
            out["time"] = "noon"
        elif "midnight" in s:
            out["time"] = "midnight"

    if "my name is " in s:
        name = s.split("my name is ", 1)[1].split(".", 1)[0].split(",", 1)[0].strip()
        out["name"] = name.title() if name else None

    # Intent extraction (health reason)
    for marker in ["for ", "about ", "regarding "]:
        if marker in s:
            intent = s.split(marker, 1)[1].split(".", 1)[0].strip()
            if intent:
                out["intent"] = intent
                break
    return out


def _parse_datetime(date_str: str, time_str: str) -> datetime:
    today = datetime.now()
    date_clean = (date_str or "").strip().lower()
    time_clean = (time_str or "").strip().lower().replace(".", ":")

    # Natural date terms
    if date_clean in ("today", "tod", "tday"):
        base_date = today
    elif date_clean in ("tomorrow", "tmr", "tmrw"):
        base_date = today + timedelta(days=1)
    else:
        base_date = None
        date_formats = ["%Y-%m-%d", "%d-%m-%Y", "%Y/%m/%d", "%d/%m/%Y"]
        for d_fmt in date_formats:
            try:
                base_date = datetime.strptime(date_str.strip(), d_fmt)
                break
            except ValueError:
                continue
        if base_date is None:
            raise ValueError("Unsupported date format. Try YYYY-MM-DD, today, or tomorrow.")

    # Natural time terms
    if time_clean in ("noon", "12 noon"):
        hour, minute = 12, 0
    elif time_clean in ("midnight", "12 midnight"):
        hour, minute = 0, 0
    else:
        parsed_time = None
        time_formats = ["%H:%M", "%I:%M %p", "%I %p", "%I%p", "%H"]
        for t_fmt in time_formats:
            try:
                parsed_time = datetime.strptime(time_clean.upper(), t_fmt)
                break
            except ValueError:
                continue
        if parsed_time is None:
            raise ValueError("Unsupported time format. Try 11 AM, 11:45 AM, or 14:30.")
        hour, minute = parsed_time.hour, parsed_time.minute

    return base_date.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _parse_date_only(date_str: str) -> datetime:
    s = (date_str or "").strip().lower()
    if s in ("today", "tod", "tday"):
        return datetime.now()
    if s in ("tomorrow", "tmr", "tmrw"):
        return datetime.now() + timedelta(days=1)
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%Y/%m/%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(date_str.strip(), fmt)
        except ValueError:
            continue
    raise ValueError("Invalid date format. Use YYYY-MM-DD, today, or tomorrow.")


def _is_valid_slot_boundary(dt: datetime) -> bool:
    if dt.minute not in (0, 45):
        return False
    return True


def _within_working_hours(dt: datetime) -> bool:
    if dt.hour < WORKDAY_START_HOUR:
        return False
    slot_end = dt + timedelta(minutes=SLOT_LENGTH_MINUTES)
    workday_end = dt.replace(hour=WORKDAY_END_HOUR, minute=0, second=0, microsecond=0)
    return slot_end <= workday_end


def _slot_strings_for_day(day: datetime) -> list[str]:
    slots: list[str] = []
    cur = day.replace(hour=WORKDAY_START_HOUR, minute=0, second=0, microsecond=0)
    end = day.replace(hour=WORKDAY_END_HOUR, minute=0, second=0, microsecond=0)
    while cur + timedelta(minutes=SLOT_LENGTH_MINUTES) <= end:
        slots.append(cur.strftime("%H:%M"))
        cur += timedelta(minutes=SLOT_LENGTH_MINUTES)
    return slots


@function_tool()
async def identify_user(
    phone_number: Annotated[str, "User phone number used as unique ID"],
    name: Annotated[str, "User full name"] = ""
) -> str:
    """Identify user by phone and optionally save their name."""
    normalized_phone = _normalize_phone(phone_number)
    if len(normalized_phone) < 10:
        return json.dumps({
            "tool": "identify_user",
            "status": "error",
            "message": "Please provide a valid 10-digit phone number."
        })

    # Safety: only accept phone if explicitly spoken by the user in recent turns.
    recent_turns = _recent_user_turns(4)
    recent_text = " ".join(recent_turns).lower()
    digit_text = "".join(ch for ch in recent_text if ch.isdigit())
    word_digit_text = _words_to_digit_string(recent_text)
    explicit_phone_mentioned = ("phone" in recent_text) or ("number" in recent_text)
    matched_from_recent = (
        normalized_phone in digit_text
        or normalized_phone in word_digit_text
        or digit_text.endswith(normalized_phone)
        or word_digit_text.endswith(normalized_phone)
    )
    if not (explicit_phone_mentioned and matched_from_recent):
        return json.dumps({
            "tool": "identify_user",
            "status": "error",
            "message": "I could not verify the phone number from your speech. Please say: My phone number is 9 8 1 1 2 2 3 3 4 8."
        })

    user_name = name.strip() if name else "Guest"
    session_data.user_phone = normalized_phone
    session_data.user_name = user_name if user_name else "Guest"

    try:
        existing = supabase.table("users").select("phone_number").eq("phone_number", normalized_phone).limit(1).execute()
        if existing.data:
            supabase.table("users").update({"name": session_data.user_name, "updated_at": datetime.utcnow().isoformat()}).eq(
                "phone_number", normalized_phone
            ).execute()
        else:
            supabase.table("users").insert({
                "phone_number": normalized_phone,
                "name": session_data.user_name,
            }).execute()
    except Exception as e:
        logger.error(f"❌ identify_user DB error: {e}")

    return json.dumps({
        "tool": "identify_user",
        "status": "success",
        "phone_number": normalized_phone,
        "name": session_data.user_name,
        "message": f"User identified as {session_data.user_name}."
    })


@function_tool()
async def fetch_slots(
    date: Annotated[str, "Date in YYYY-MM-DD format"]
) -> str:
    """Return available 45-minute slots for a given date within 10:00 to 17:00."""
    try:
        day = _parse_date_only(date)
    except ValueError:
        return json.dumps({
            "tool": "fetch_slots",
            "status": "error",
            "message": "Invalid date format. Use YYYY-MM-DD, today, or tomorrow."
        })

    all_slots = _slot_strings_for_day(day)
    try:
        start_iso = day.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        end_iso = (day + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        booked_resp = supabase.table("appointments").select("appointment_time").eq(
            "status", "confirmed"
        ).gte("appointment_time", start_iso).lt("appointment_time", end_iso).execute()
        booked_times = set()
        for appt in (booked_resp.data or []):
            try:
                dt = datetime.fromisoformat(appt["appointment_time"].replace("Z", "+00:00"))
                booked_times.add(dt.strftime("%H:%M"))
            except Exception:
                continue
        available = [s for s in all_slots if s not in booked_times]
    except Exception as e:
        logger.error(f"❌ fetch_slots DB error: {e}")
        available = all_slots

    return json.dumps({
        "tool": "fetch_slots",
        "status": "success",
        "date": date,
        "available_slots": available,
        "message": "Fetched available slots."
    })


@function_tool()
async def book_appointment(
    date: Annotated[str, "Date in YYYY-MM-DD format"],
    time_slot: Annotated[str, "Time in HH:MM or HH:MM AM/PM"],
    intent: Annotated[str, "Reason for appointment"],
    name: Annotated[str, "User name"] = "",
    phone_number: Annotated[str, "User phone number"] = "",
) -> str:
    """Book an appointment, prevent double booking, and confirm details."""
    phone = _normalize_phone(phone_number or session_data.user_phone or "")
    if not phone:
        return json.dumps({
            "tool": "book_appointment",
            "status": "error",
            "message": "Please identify the user with phone number before booking."
        })
    session_data.user_phone = phone
    if name.strip():
        session_data.user_name = name.strip()

    try:
        appointment_dt = _parse_datetime(date, time_slot)
    except ValueError as e:
        return json.dumps({"tool": "book_appointment", "status": "error", "message": str(e)})

    if appointment_dt < datetime.now():
        return json.dumps({"tool": "book_appointment", "status": "error", "message": "Cannot book a past time slot."})
    if not _is_valid_slot_boundary(appointment_dt):
        return json.dumps({"tool": "book_appointment", "status": "error", "message": "Slots must start at :00 or :45."})
    if not _within_working_hours(appointment_dt):
        return json.dumps({
            "tool": "book_appointment",
            "status": "error",
            "message": "Slot must be within working hours: 10:00 to 17:00."
        })

    try:
        user_resp = supabase.table("users").select("id").eq("phone_number", phone).limit(1).execute()
        if user_resp.data:
            user_id = user_resp.data[0]["id"]
            supabase.table("users").update({"name": session_data.user_name or "Guest"}).eq("id", user_id).execute()
        else:
            inserted_user = supabase.table("users").insert({
                "phone_number": phone,
                "name": session_data.user_name or "Guest",
            }).execute()
            user_id = inserted_user.data[0]["id"]

        slot_iso = appointment_dt.isoformat()
        existing_slot = supabase.table("appointments").select("id").eq(
            "appointment_time", slot_iso
        ).eq("status", "confirmed").limit(1).execute()
        if existing_slot.data:
            return json.dumps({
                "tool": "book_appointment",
                "status": "error",
                "message": "That slot is already booked. Please pick another time."
            })

        inserted = supabase.table("appointments").insert({
            "user_id": user_id,
            "phone_number": phone,
            "user_name": session_data.user_name or "Guest",
            "appointment_time": slot_iso,
            "intent": intent.strip(),
            "status": "confirmed",
        }).execute()
        appointment = inserted.data[0]
        session_data.appointments.append(appointment)
    except Exception as e:
        logger.error(f"❌ book_appointment DB error: {e}")
        return json.dumps({"tool": "book_appointment", "status": "error", "message": "Failed to book appointment due to DB error."})

    return json.dumps({
        "tool": "book_appointment",
        "status": "success",
        "appointment_id": appointment["id"],
        "date": appointment_dt.strftime("%Y-%m-%d"),
        "time": appointment_dt.strftime("%H:%M"),
        "intent": intent.strip(),
        "message": f"Booking confirmed for {appointment_dt.strftime('%Y-%m-%d %H:%M')}."
    })


@function_tool()
async def retrieve_appointments(
    phone_number: Annotated[str, "User phone number"] = ""
) -> str:
    """Retrieve appointments for a user by phone number."""
    phone = _normalize_phone(phone_number or session_data.user_phone or "")
    if not phone:
        return json.dumps({
            "tool": "retrieve_appointments",
            "status": "error",
            "message": "Please provide a phone number."
        })

    try:
        resp = supabase.table("appointments").select(
            "id, appointment_time, status, intent, user_name"
        ).eq("phone_number", phone).order("appointment_time", desc=False).execute()
        appointments = resp.data or []
    except Exception as e:
        logger.error(f"❌ retrieve_appointments DB error: {e}")
        appointments = []

    formatted = []
    for appt in appointments:
        try:
            dt = datetime.fromisoformat(appt["appointment_time"].replace("Z", "+00:00"))
            formatted.append({
                "id": appt["id"],
                "date": dt.strftime("%Y-%m-%d"),
                "time": dt.strftime("%H:%M"),
                "status": appt.get("status"),
                "intent": appt.get("intent"),
                "name": appt.get("user_name")
            })
        except Exception:
            formatted.append(appt)

    return json.dumps({
        "tool": "retrieve_appointments",
        "status": "success",
        "phone_number": phone,
        "appointments": formatted,
        "message": f"Found {len(formatted)} appointments."
    })


@function_tool()
async def cancel_appointment(
    appointment_id: Annotated[str, "Appointment UUID to cancel"]
) -> str:
    """Cancel appointment if it is more than 1 hour away."""
    try:
        match = supabase.table("appointments").select(
            "id, appointment_time, status"
        ).eq("id", appointment_id).limit(1).execute()
        if not match.data:
            return json.dumps({"tool": "cancel_appointment", "status": "error", "message": "Appointment not found."})

        appt = match.data[0]
        appt_dt = datetime.fromisoformat(appt["appointment_time"].replace("Z", "+00:00"))
        if appt_dt - datetime.now(appt_dt.tzinfo) < timedelta(minutes=NO_CHANGE_WINDOW_MINUTES):
            return json.dumps({
                "tool": "cancel_appointment",
                "status": "error",
                "message": "Cannot cancel within 1 hour of appointment time."
            })

        supabase.table("appointments").update({"status": "cancelled"}).eq("id", appointment_id).execute()
    except Exception as e:
        logger.error(f"❌ cancel_appointment DB error: {e}")
        return json.dumps({"tool": "cancel_appointment", "status": "error", "message": "Failed to cancel appointment."})

    return json.dumps({
        "tool": "cancel_appointment",
        "status": "success",
        "appointment_id": appointment_id,
        "message": "Appointment cancelled successfully."
    })


@function_tool()
async def modify_appointment(
    appointment_id: Annotated[str, "Appointment UUID to modify"],
    new_date: Annotated[str, "New date in YYYY-MM-DD"],
    new_time: Annotated[str, "New time in HH:MM or HH:MM AM/PM"],
) -> str:
    """Reschedule appointment if more than 1 hour remains, slot is valid, and no clash exists."""
    try:
        new_dt = _parse_datetime(new_date, new_time)
    except ValueError as e:
        return json.dumps({"tool": "modify_appointment", "status": "error", "message": str(e)})

    if not _is_valid_slot_boundary(new_dt):
        return json.dumps({"tool": "modify_appointment", "status": "error", "message": "Slots must start at :00 or :45."})
    if not _within_working_hours(new_dt):
        return json.dumps({"tool": "modify_appointment", "status": "error", "message": "New slot must be within 10:00 to 17:00."})

    try:
        existing = supabase.table("appointments").select(
            "id, appointment_time, status"
        ).eq("id", appointment_id).limit(1).execute()
        if not existing.data:
            return json.dumps({"tool": "modify_appointment", "status": "error", "message": "Appointment not found."})

        appt = existing.data[0]
        appt_dt = datetime.fromisoformat(appt["appointment_time"].replace("Z", "+00:00"))
        if appt_dt - datetime.now(appt_dt.tzinfo) < timedelta(minutes=NO_CHANGE_WINDOW_MINUTES):
            return json.dumps({
                "tool": "modify_appointment",
                "status": "error",
                "message": "Cannot modify within 1 hour of appointment time."
            })

        clash = supabase.table("appointments").select("id").eq(
            "appointment_time", new_dt.isoformat()
        ).eq("status", "confirmed").neq("id", appointment_id).limit(1).execute()
        if clash.data:
            return json.dumps({
                "tool": "modify_appointment",
                "status": "error",
                "message": "Requested new slot is already booked."
            })

        supabase.table("appointments").update({
            "appointment_time": new_dt.isoformat(),
            "status": "confirmed",
        }).eq("id", appointment_id).execute()
    except Exception as e:
        logger.error(f"❌ modify_appointment DB error: {e}")
        return json.dumps({"tool": "modify_appointment", "status": "error", "message": "Failed to modify appointment."})

    return json.dumps({
        "tool": "modify_appointment",
        "status": "success",
        "appointment_id": appointment_id,
        "new_date": new_dt.strftime("%Y-%m-%d"),
        "new_time": new_dt.strftime("%H:%M"),
        "message": f"Appointment moved to {new_dt.strftime('%Y-%m-%d %H:%M')}."
    })


@function_tool()
async def capture_preference(
    preference: Annotated[str, "User preference such as doctor, time preference, language, contact method"]
) -> str:
    """Capture and store user preferences for summary/use in later turns."""
    pref = (preference or "").strip()
    if not pref:
        return json.dumps({
            "tool": "capture_preference",
            "status": "error",
            "message": "No preference provided."
        })
    if pref not in session_data.preferences:
        session_data.preferences.append(pref)
    return json.dumps({
        "tool": "capture_preference",
        "status": "success",
        "preference": pref,
        "message": "Preference noted."
    })

# -------------------------
# Tool Functions
# -------------------------



@function_tool()
async def end_conversation(
    confirmation: Annotated[str, "Say 'yes' to confirm ending the conversation"] = "yes"
) -> str:
    """End the conversation and generate a summary."""
    
    # -------------------------
    # PRINT TRANSCRIPT DIRECTLY
    # -------------------------
    print("\n" + "="*40)
    print("📜 FINAL TRANSCRIPT (Bypassing Frontend)")
    print("="*40)
    for t in session_data.full_transcript:
        role = t.get('role', 'unknown').upper()
        txt = t.get('text', '')
        print(f"[{role}]: {txt}")
    print("="*40 + "\n")
    
    duration = (datetime.now() - session_data.session_start).seconds
    cost_data = get_cost_breakdown()
    
    # Generate high-quality summary via LLM using full transcript
    transcript_text = "\n".join([f"{t['role']}: {t['text']}" for t in session_data.full_transcript])
    
    summary_text = "No interaction recorded."
    if transcript_text:
        try:
            # We use the agent's LLM to generate the summary
            # We need to do this carefully within the tool context
            # Since this is an async tool, we can await the LLM
            
            summary_prompt = f"""Summarize this conversation based on the transcript below. 
            Include:
            1. Main topic discussed
            2. Key questions asked by the user
            3. Answers provided by the assistant
            4. Any follow-up needed
            
            Transcript:
            {transcript_text}
            
            Keep it professional and concise (max 150 words)."""
            
            # Use Cerebras/OpenAI directly for a quick chat completion
            from openai import AsyncOpenAI
            client = AsyncOpenAI(
                base_url="https://api.cerebras.ai/v1",
                api_key=os.environ["CEREBRAS_API_KEY"]
            )
            
            response = await client.chat.completions.create(
                model=os.environ.get("CEREBRAS_MODEL", "llama3.1-8b"),
                messages=[{"role": "system", "content": "You are a professional assistant summarizing a call."},
                          {"role": "user", "content": summary_prompt}]
            )
            summary_text = response.choices[0].message.content
        except Exception as e:
            logger.error(f"❌ Summary generation failed: {e}")
            summary_text = f"Conversational summary could not be generated, but your call lasted {duration}s."

    ended_at = datetime.now().isoformat()
    appointments_booked = [a for a in session_data.appointments if a.get("status") == "confirmed"]

    summary_data = {
        "user_phone": session_data.user_phone,
        "user_name": session_data.user_name,
        "duration_seconds": duration,
        "appointments_booked": appointments_booked,
        "preferences": session_data.preferences,
        "transcript": session_data.full_transcript,
        "cost_breakdown": cost_data,
        "summary_text": summary_text,
        "timestamp": ended_at,
    }

    try:
        insert_data = {
            "user_phone": session_data.user_phone,
            "user_name": session_data.user_name,
            "duration_seconds": duration,
            "transcript": session_data.full_transcript,
            "appointments_booked": appointments_booked,
            "preferences": session_data.preferences,
            "cost_breakdown": cost_data,
            "summary_text": summary_text,
            "ended_at": ended_at,
        }
        
        supabase.table("call_summaries").insert(insert_data).execute()
        logger.info(f"💾 Call summary saved to DB")
    except Exception as e:
        logger.error(f"❌ Failed to save summary to DB: {e}")
    
    return json.dumps({
        "tool": "end_conversation",
        "status": "success",
        "data": summary_data,
        "message": "Thank you! Your session is ending."
    })


# -------------------------
# Entrypoint
# -------------------------
async def entrypoint(ctx: JobContext):
    global session_data
    session_data = SessionData()  # Fresh session data
    
    start = time.time()
    
    # Connect to room
    await ctx.connect(auto_subscribe=AutoSubscribe.SUBSCRIBE_ALL)
    
    # Find real user participant (avoid binding to Beyond avatar participant)
    participants = list(ctx.room.remote_participants.values())
    participant = next(
        (
            p for p in participants
            if not str(getattr(p, "identity", "")).startswith("bey-avatar-")
            and str(getattr(p, "identity", "")).startswith("user-")
        ),
        None,
    )
    if not participant:
        participant = next(
            (p for p in participants if not str(getattr(p, "identity", "")).startswith("bey-avatar-")),
            None,
        )
    if not participant:
        while True:
            candidate = await ctx.wait_for_participant()
            cid = str(getattr(candidate, "identity", ""))
            if not cid.startswith("bey-avatar-"):
                participant = candidate
                break
    
    logger.info(f"⚡ Connected in {(time.time()-start)*1000:.0f}ms | Participant: {participant.identity}")

    # Read room metadata for custom system prompt and greeting
    custom_prompt = None
    custom_greeting = None
    if ctx.room.metadata:
        try:
            room_meta = json.loads(ctx.room.metadata)
            custom_prompt = room_meta.get("systemPrompt")
            custom_greeting = room_meta.get("firstMessage")
            if custom_prompt:
                logger.info(f"📝 Custom system prompt received ({len(custom_prompt)} chars)")
            if custom_greeting:
                logger.info(f"👋 Custom greeting: {custom_greeting[:50]}...")
        except (json.JSONDecodeError, TypeError):
            logger.warning("⚠️ Could not parse room metadata")

    # Create services (STT, LLM, TTS)
    stt = deepgram.STT(
        api_key=os.environ["DEEPGRAM_API_KEY"],
        model="nova-2-phonecall",
    )
    
    llm = _build_llm()
    
    tts = cartesia.TTS(
        api_key=os.environ["CARTESIA_API_KEY"],
        voice="6786ecbd-b414-479d-b4ce-d500f2961556",
    )

    # Agent instructions — use custom prompt if provided, otherwise default
    # -------------------------
    # Healthcare Assistant Instructions
    # -------------------------
    base_system_prompt = f"""You are a healthcare front-desk voice assistant.

Primary responsibilities:
1. Identify users using phone number (unique ID) and collect name.
2. Handle appointment tasks using tools:
   - identify_user
   - fetch_slots
   - book_appointment
   - retrieve_appointments
   - cancel_appointment
   - modify_appointment
   - capture_preference
   - end_conversation
3. Extract and confirm: name, phone number, date, time, and intent before booking.
3a. Never guess or invent name/phone. If missing, ask user explicitly.
3b. Do not call identify_user unless the user explicitly provides a phone number in that turn.
4. Working hours are 10:00 to 17:00, slot length is 45 minutes.
5. No cancellation/modification allowed within 1 hour of appointment time.
6. Always confirm final booking details clearly.
7. Keep responses concise, warm, and natural for voice.
8. If user asks general questions like "what can you do?", answer that directly first. Do not ask for personal details unless user asks to book/retrieve/modify/cancel.

Use knowledge-base context if available for informational questions, but do not block appointment handling on KB lookups.
Today's date is {datetime.now().strftime("%Y-%m-%d")}.
"""

    if custom_prompt:
        instructions = f"{base_system_prompt}\n\n--- User Context / Persona ---\n{custom_prompt}"
    else:
        instructions = base_system_prompt + """
Tone:
- Helpful, concise, and spoken-style.
- Natural conversation (but strictly grounded in the provided context).
"""

    # Broadcast helper
    async def broadcast(data):
        try:
            await ctx.room.local_participant.publish_data(json.dumps(data), reliable=True)
        except:
            pass

    # RAG retrieval — queries the KB API automatically before each LLM call
    KB_API_URL = os.environ.get("KB_API_URL", "http://localhost:8001")

    async def _query_kb(query: str) -> tuple[str, list]:
        """Query the knowledge base and return (context_text, sources_for_ui)."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{KB_API_URL}/api/kb/query",
                    json={"query": query, "top_k": 2},
                )
                data = resp.json()
                results = data.get("results", [])

                if not results:
                    return "No relevant documents found in the knowledge base.", []

                context_parts = []
                sources_for_ui = []
                for r in results:
                    source = r["metadata"]["doc_name"]
                    text = r["text"]
                    if len(text) > 320:
                        text = text[:320] + "..."
                    context_parts.append(f"[Source: {source}]\n{text}")
                    sources_for_ui.append({
                        "doc_name": source,
                        "snippet": text[:150] + ("..." if len(text) > 150 else ""),
                        "chunk_index": r["metadata"].get("chunk_index", 0),
                        "relevance": round(1 - r.get("distance", 0), 2),
                    })

                logger.info(f"🔍 KB search: '{query[:40]}...' → {len(results)} results")
                return "\n\n---\n\n".join(context_parts), sources_for_ui
        except Exception as e:
            logger.error(f"❌ KB search failed: {e}")
            return f"Knowledge base search failed: {str(e)}", []

    class RAGAgent(Agent):
        async def on_user_turn_completed(self, turn_ctx, new_message):
            """Inject KB context after the user turn and before LLM response."""
            content = getattr(new_message, "content", None)
            if not content:
                return

            if isinstance(content, list):
                user_msg = " ".join(str(c) for c in content if c).strip()
            else:
                user_msg = str(content).strip()

            if not user_msg:
                return
            await _throttle_for_tpm()

            context_text, sources = await _query_kb(user_msg)

            if sources:
                await broadcast({
                    "type": "rag_sources",
                    "query": user_msg,
                    "sources": sources,
                })

            turn_ctx.items.append(
                ChatMessage(role="system", content=[f"KNOWLEDGE BASE CONTEXT (brief):\n{context_text}"])
            )

    # Create agent WITH tools
    agent = RAGAgent(
        instructions=instructions,
        tools=[
            identify_user,
            fetch_slots,
            book_appointment,
            retrieve_appointments,
            cancel_appointment,
            modify_appointment,
            capture_preference,
            end_conversation,
        ],
        stt=stt,
        llm=llm,
        tts=tts,
    )

    # Create session
    session = AgentSession()
    


    # Summary generator function
    async def generate_final_summary():
        if session_data.summary_sent:
            return
        
        session_data.summary_sent = True
        try:
            logger.info("📊 Generating final summary...")
            summary_json = await end_conversation("yes")
            summary_data = json.loads(summary_json)
            await broadcast(summary_data)
            logger.info("✅ Final summary broadcasted")
        except Exception as e:
            logger.error(f"❌ Failed to generate end summary: {e}")

    # Listen for signals from frontend
    @ctx.room.on("data_received")
    def on_data_received(data, participant=None, kind=None, topic=None):
        async def _handle():
            try:
                # Handle LiveKit DataPacket object (v1.3+ changes)
                if hasattr(data, 'data'):
                    payload_bytes = data.data
                elif isinstance(data, bytes):
                    payload_bytes = data
                else:
                    return

                try:
                    payload = json.loads(payload_bytes.decode())
                except:
                    return

                if payload.get("action") == "end_session":
                    logger.info("🛑 End session signal received")
                    try:
                        await ctx.room.disconnect()
                    except Exception as dc_err:
                        logger.warning(f"Disconnect error (non-fatal): {dc_err}")

            except Exception as e:
                logger.error(f"Data receive error: {e}")

        asyncio.create_task(_handle())

    # -------------------------
    # Usage Tracking & Transcript Capture (v1.3.11 correct event names)
    # -------------------------
    @session.on("user_input_transcribed")
    def on_user_input_transcribed(ev):
        # ev is UserInputTranscribedEvent with .transcript (str) and .is_final (bool)
        if ev.is_final and ev.transcript:
            logger.info(f"🗣️ USER: {ev.transcript}")
            entities = _extract_entities_from_text(ev.transcript)
            if entities.get("phone") and not session_data.user_phone:
                session_data.user_phone = entities["phone"]
            if entities.get("name") and (not session_data.user_name or session_data.user_name == "Guest"):
                session_data.user_name = entities["name"]
            if entities.get("intent"):
                intent_pref = f"Visit reason: {entities['intent']}"
                if intent_pref not in session_data.preferences:
                    session_data.preferences.append(intent_pref)
            session_data.full_transcript.append({
                "role": "user",
                "text": ev.transcript,
                "time": time.time()
            })
            session_data.usage["stt_seconds"] += len(ev.transcript) / 15.0
            asyncio.create_task(broadcast({"role": "user", "text": ev.transcript}))

    @session.on("conversation_item_added")
    def on_conversation_item_added(ev):
        # ev is ConversationItemAddedEvent with .item (ChatMessage)
        item = ev.item
        role = getattr(item, "role", None)
        if role and str(role) == "assistant":
            content = getattr(item, "content", None)
            if content:
                if isinstance(content, str):
                    text = content
                elif isinstance(content, list):
                    text = " ".join(str(c) for c in content if c)
                else:
                    text = str(content)

                if text.strip():
                    logger.info(f"🤖 AGENT: {text}")
                    session_data.full_transcript.append({
                        "role": "agent",
                        "text": text,
                        "time": time.time()
                    })
                    session_data.usage["tts_chars"] += len(text)
                    asyncio.create_task(broadcast({"role": "agent", "text": text}))

    @session.on("metrics_collected")
    def on_metrics(ev):
        m = ev.metrics
        if m.type == "stt_metrics":
            logger.info(f"📊 STT: audio={m.audio_duration:.1f}s | streamed={m.streamed}")
        elif m.type == "tts_metrics":
            logger.info(f"📊 TTS: chars={m.characters_count} | ttfb={m.ttfb:.3f}s | audio={m.audio_duration:.1f}s | cancelled={m.cancelled}")
        elif m.type == "llm_metrics":
            logger.info(f"📊 LLM: prompt={m.prompt_tokens} | completion={m.completion_tokens} | total={m.total_tokens} | tok/s={m.tokens_per_second:.1f} | ttft={m.ttft:.3f}s")
            session_data.usage["llm_input_tokens"] += m.prompt_tokens
            session_data.usage["llm_output_tokens"] += m.completion_tokens
            session_data.llm_token_events.append((time.time(), int(m.total_tokens or 0)))
            _prune_llm_window()
        elif m.type == "vad_metrics":
            logger.info(f"📊 VAD: idle={m.idle_time:.1f}s | inferences={m.inference_count} | total_dur={m.inference_duration_total:.3f}s")
        elif m.type == "eou_metrics":
            logger.info(f"📊 EOU: utterance_delay={m.end_of_utterance_delay:.3f}s | transcription_delay={m.transcription_delay:.3f}s | turn_completed_delay={m.on_user_turn_completed_delay:.3f}s")

    @session.on("error")
    def on_error(ev):
        logger.error(f"❗ SESSION ERROR: source={ev.source} | error={ev.error}")

    @session.on("close")
    def on_close(ev):
        logger.info(f"🔒 SESSION CLOSED: reason={ev.reason} | error={ev.error}")
        # Auto-generate summary when session closes
        if not session_data.summary_sent:
            asyncio.create_task(generate_final_summary())

    @session.on("function_tools_executed")
    def on_tools_executed(ev):
        for call, output in ev.zipped():
            status = "✅" if output else "⚠️"
            logger.info(f"🔧 TOOL {status}: {call.name}({call.arguments})")
            try:
                payload = None
                if isinstance(output, str):
                    try:
                        parsed = json.loads(output)
                        if isinstance(parsed, dict):
                            payload = parsed
                    except json.JSONDecodeError:
                        payload = {
                            "tool": call.name,
                            "status": "success",
                            "message": output,
                        }
                elif isinstance(output, dict):
                    payload = output

                if payload is None:
                    payload = {
                        "tool": call.name,
                        "status": "success",
                        "message": "Tool executed.",
                    }

                if "tool" not in payload:
                    payload["tool"] = call.name
                if "status" not in payload:
                    payload["status"] = "success"

                asyncio.create_task(broadcast(payload))
            except Exception as e:
                logger.error(f"❌ Failed to broadcast tool payload: {e}")

    @session.on("user_state_changed")
    def on_user_state(ev):
        logger.debug(f"👤 User state: {ev.old_state} → {ev.new_state}")

    @session.on("agent_state_changed")
    def on_agent_state(ev):
        logger.debug(f"🤖 Agent state: {ev.old_state} → {ev.new_state}")

    @session.on("speech_created")
    def on_speech_created(ev):
        logger.debug(f"🎙️ Speech created: source={ev.source} | user_initiated={ev.user_initiated}")

    # Start agent session
    await session.start(
        agent,
        room=ctx.room,
        room_options=RoomOptions(
            participant_identity=participant.identity,
            close_on_disconnect=False
        ),
    )
    
    # Greet the user automatically
    greeting = custom_greeting or "Hello! I'm your voice AI assistant. How can I help you today?"
    await session.say(greeting, allow_interruptions=True)
    
    logger.info(f"🎯 Voice READY in {(time.time()-start)*1000:.0f}ms")
    
    # -------------------------
    # Session End Handler - Auto-generate summary
    # -------------------------
    def on_agent_stopped():
        """When session ends, generate summary if not already sent"""
        if not session_data.summary_sent:
            asyncio.create_task(generate_final_summary())

    session.on("agent_stopped", on_agent_stopped)


# -------------------------
# Run worker
# -------------------------
if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            agent_name="voice-agent",
        ),
    )
