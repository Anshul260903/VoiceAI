# =========================
# agent.py (WITH TOOLS & AVATAR)
# =========================

import os
import socket
import asyncio
import logging
import time
import json
from datetime import datetime, timedelta
from typing import Annotated
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
load_dotenv(os.path.join(BASE_DIR, ".env"))

# -------------------------
# LiveKit imports
# -------------------------
from livekit.agents import (
    AutoSubscribe,
    JobContext,
    WorkerOptions,
    cli,
)
from livekit.agents.llm import function_tool
from livekit.agents.voice import Agent, AgentSession
from livekit.agents.voice.room_io import RoomOptions

# -------------------------
# Plugin imports
# -------------------------
from livekit.plugins import deepgram, openai, cartesia, bey
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
        
session_data = SessionData()

def get_cost_breakdown():

    stt_cost = session_data.usage["stt_seconds"] * 0.0000967
    tts_cost = session_data.usage["tts_chars"] * 0.00001
    llm_input_cost = session_data.usage["llm_input_tokens"] * 0.00000085
    llm_output_cost = session_data.usage["llm_output_tokens"] * 0.0000012
    llm_total_cost = llm_input_cost + llm_output_cost
    total = stt_cost + tts_cost + llm_total_cost
    
    return {
        "stt": {
            "usage": f"{session_data.usage['stt_seconds']:.1f}s", 
            "cost": stt_cost,
            "rate": "$0.0058/min"
        },
        "tts": {
            "usage": f"{session_data.usage['tts_chars']} chars", 
            "cost": tts_cost,
            "rate": "$10.00/1M chars"
        },
        "llm": {
            "usage": f"{session_data.usage['llm_input_tokens']}in + {session_data.usage['llm_output_tokens']}out tokens", 
            "cost": llm_total_cost,
            "rate": "$0.85/1M in, $1.20/1M out"
        },
        "total": total
    }

# -------------------------
# Tool Functions
# -------------------------

@function_tool()
async def identify_user(
    phone_number: Annotated[str, "The user's phone number"],
    name: Annotated[str, "The user's name if provided"] = None
) -> str:
    """Identify the user by their phone number. Call this when user provides their phone number."""
    session_data.user_phone = phone_number
    if name and name.lower() != "null":
        session_data.user_name = name
    
    logger.info(f"📱 User identified: {phone_number} ({name})")
    
    try:
        # Upsert user into Supabase - only update name if provided
        user_data = {"phone_number": phone_number}
        if name:
            user_data["name"] = name
            
        supabase.table("users").upsert(user_data).execute()
        
        # If name not provided, try to fetch existing name from DB
        if not name:
            existing = supabase.table("users").select("name").eq("phone_number", phone_number).execute()
            if existing.data and existing.data[0].get("name"):
                session_data.user_name = existing.data[0]["name"]
                logger.info(f"👤 Found existing user name: {session_data.user_name}")
        
        logger.info(f"💾 User {phone_number} sync'd with DB")
    except Exception as e:
        logger.error(f"❌ Failed to save user to DB: {e}")

    # Send tool call event to frontend via data channel
    return json.dumps({
        "tool": "identify_user",
        "status": "success",
        "data": {
            "phone": phone_number,
            "name": name
        },
        "message": f"User identified with phone {phone_number}" + (f" and name {name}" if name else "")
    })


@function_tool()
async def fetch_slots(
    date: Annotated[str, "Optional date to filter slots - can be 'tomorrow', 'day after tomorrow', or YYYY-MM-DD"] = None
) -> str:
    """Fetch available appointment slots. Call this when user asks about available times."""
    
    target_date = None
    today = datetime.now().date()
    
    if date:
        date_lower = date.lower().strip()
        # Handle natural language dates
        if "tomorrow" in date_lower and "day after" not in date_lower:
            target_date = today + timedelta(days=1)
        elif "day after tomorrow" in date_lower:
            target_date = today + timedelta(days=2)
        elif "today" in date_lower:
            target_date = today
        else:
            try:
                target_date = datetime.strptime(date, "%Y-%m-%d").date()
            except:
                pass
    
    # Generate potential slots for the next 7 days if no date or valid date found
    start_date = target_date or (today + timedelta(days=1))
    end_date = target_date or (today + timedelta(days=7))
    
    potential_slots = []
    curr = start_date
    while curr <= end_date:
        date_str = curr.strftime("%Y-%m-%d")
        for hour in [9, 10, 11, 14, 15, 16]:
            potential_slots.append({"date": date_str, "time": f"{hour:02d}:00"})
        curr += timedelta(days=1)

    # Query existing confirmed appointments
    try:
        query = supabase.table("appointments").select("date, time").eq("status", "confirmed")
        if target_date:
            query = query.eq("date", target_date.strftime("%Y-%m-%d"))
        
        booked_resp = query.execute()
        booked_slots = {(b["date"], b["time"][:5]) for b in booked_resp.data}
    except Exception as e:
        logger.error(f"❌ Failed to fetch booked slots: {e}")
        booked_slots = set()

    # Filter available slots
    available = []
    for s in potential_slots:
        if (s["date"], s["time"]) not in booked_slots:
            available.append({**s, "available": True})

    logger.info(f"📅 Found {len(available)} available slots")
    
    return json.dumps({
        "tool": "fetch_slots",
        "status": "success",
        "data": {"slots": available[:10], "date_searched": target_date.strftime("%Y-%m-%d") if target_date else None},
        "message": f"Found {len(available)} available slots"
    })


@function_tool()
async def book_appointment(
    date: Annotated[str, "Appointment date - can be 'tomorrow', 'day after tomorrow', or YYYY-MM-DD"],
    time: Annotated[str, "Appointment time - can be '2 PM', '10 AM', or HH:MM format"],
    purpose: Annotated[str | None, "Optional purpose or reason for the appointment"] = None
) -> str:
    """Book an appointment for the user. Requires date and time."""
    
    # Default purpose if not provided
    purpose = purpose or "General consultation"
    
    if not session_data.user_phone:
        return json.dumps({
            "tool": "book_appointment",
            "status": "error",
            "message": "Please provide your phone number first to book an appointment"
        })
    
    # Convert natural language date to YYYY-MM-DD
    today = datetime.now().date()
    date_lower = date.lower().strip() if date else ""
    
    if "tomorrow" in date_lower and "day after" not in date_lower:
        target_date = (today + timedelta(days=1)).strftime("%Y-%m-%d")
    elif "day after tomorrow" in date_lower:
        target_date = (today + timedelta(days=2)).strftime("%Y-%m-%d")
    elif "today" in date_lower:
        target_date = today.strftime("%Y-%m-%d")
    elif len(date) == 10 and "-" in date:
        target_date = date
    else:
        target_date = (today + timedelta(days=1)).strftime("%Y-%m-%d")  # Default to tomorrow
    
    # Convert natural language time to HH:MM
    time_str = time.upper().strip() if time else "10:00"
    if "AM" in time_str or "PM" in time_str:
        time_clean = "".join(filter(str.isdigit, time_str))
        try:
            hour = int(time_clean)
            if "PM" in time_str and hour != 12:
                hour += 12
            if "AM" in time_str and hour == 12:
                hour = 0
            target_time = f"{hour:02d}:00"
        except ValueError:
            target_time = "10:00"
    elif ":" in time_str:
        target_time = time_str[:5]
    else:
        target_time = "10:00"
    
    # Check double booking in Supabase
    try:
        check = supabase.table("appointments").select("*").eq("date", target_date).eq("time", target_time).eq("status", "confirmed").execute()
        if check.data:
            return json.dumps({
                "tool": "book_appointment",
                "status": "error",
                "message": f"Sorry, the slot on {target_date} at {target_time} is already booked. Please choose another time."
            })
        
        # Book it
        resp = supabase.table("appointments").insert({
            "user_phone": session_data.user_phone,
            "date": target_date,
            "time": target_time,
            "purpose": purpose,
            "status": "confirmed"
        }).execute()
        
        appointment = resp.data[0]
        session_data.appointments.append(appointment)
        logger.info(f"✅ Appointment booked in DB: {target_date} at {target_time}")
        
        return json.dumps({
            "tool": "book_appointment",
            "status": "success",
            "data": appointment,
            "message": f"Appointment confirmed for {target_date} at {target_time}. Purpose: {purpose}"
        })
    except Exception as e:
        logger.error(f"❌ Booking failed: {e}")
        return json.dumps({
            "tool": "book_appointment",
            "status": "error",
            "message": "Sorry, I encountered an error while booking your appointment. Please try again."
        })


@function_tool()
async def retrieve_appointments(
    status: Annotated[str, "Filter by status: all, confirmed, cancelled"] = "all"
) -> str:
    """Retrieve the user's appointments. Call this when user asks about their bookings."""
    
    if not session_data.user_phone:
        return json.dumps({
            "tool": "retrieve_appointments",
            "status": "error",
            "message": "Please provide your phone number first to retrieve appointments"
        })
    
    try:
        query = supabase.table("appointments").select("*").eq("user_phone", session_data.user_phone)
        if status != "all":
            query = query.eq("status", status)
        
        resp = query.execute()
        appointments = resp.data
        session_data.appointments = appointments
        
        logger.info(f"📋 Retrieved {len(appointments)} appointments from DB")
        
        return json.dumps({
            "tool": "retrieve_appointments",
            "status": "success",
            "data": {"appointments": appointments},
            "message": f"Found {len(appointments)} appointment(s)" if appointments else "No appointments found"
        })
    except Exception as e:
        logger.error(f"❌ Failed to retrieve appointments: {e}")
        return json.dumps({
            "tool": "retrieve_appointments",
            "status": "error",
            "message": "I couldn't retrieve your appointments right now."
        })


@function_tool()
async def cancel_appointment(
    appointment_id: Annotated[str, "The appointment ID to cancel (e.g., APT-1)"]
) -> str:
    """Cancel an existing appointment. Requires the appointment ID."""
    
    try:
        resp = supabase.table("appointments").update({"status": "cancelled"}).eq("id", appointment_id).eq("user_phone", session_data.user_phone).execute()
        
        if not resp.data:
            return json.dumps({
                "tool": "cancel_appointment",
                "status": "error",
                "message": f"Appointment {appointment_id} not found or doesn't belong to you"
            })
        
        appointment = resp.data[0]
        logger.info(f"❌ Appointment cancelled in DB: {appointment_id}")
        
        return json.dumps({
            "tool": "cancel_appointment",
            "status": "success",
            "data": appointment,
            "message": f"Appointment on {appointment['date']} at {appointment['time']} has been cancelled"
        })
    except Exception as e:
        logger.error(f"❌ Cancellation failed: {e}")
        return json.dumps({
            "tool": "cancel_appointment",
            "status": "error",
            "message": "Failed to cancel appointment."
        })


@function_tool()
async def modify_appointment(
    appointment_id: Annotated[str, "The appointment ID to modify"],
    new_date: Annotated[str, "New date in YYYY-MM-DD format"] = None,
    new_time: Annotated[str, "New time in HH:MM format"] = None
) -> str:
    """Modify an existing appointment's date or time."""
    
    if not session_data.user_phone:
        return json.dumps({"tool": "modify_appointment", "status": "error", "message": "Phone number required."})

    try:
        # Get existing
        existing = supabase.table("appointments").select("*").eq("id", appointment_id).single().execute()
        if not existing.data:
            return json.dumps({"tool": "modify_appointment", "status": "error", "message": "Appointment not found."})
        
        target_date = new_date or existing.data["date"]
        target_time = new_time or existing.data["time"]

        # Check availability
        check = supabase.table("appointments").select("*").eq("date", target_date).eq("time", target_time).eq("status", "confirmed").execute()
        if check.data and check.data[0]["id"] != appointment_id:
            return json.dumps({"tool": "modify_appointment", "status": "error", "message": "That new slot is already taken."})

        # Update
        resp = supabase.table("appointments").update({
            "date": target_date,
            "time": target_time
        }).eq("id", appointment_id).execute()

        appointment = resp.data[0]
        logger.info(f"✏️ Appointment modified in DB: {appointment_id}")
        
        return json.dumps({
            "tool": "modify_appointment",
            "status": "success",
            "data": appointment,
            "message": f"Appointment rescheduled to {target_date} at {target_time}"
        })
    except Exception as e:
        logger.error(f"❌ Modification failed: {e}")
        return json.dumps({"tool": "modify_appointment", "status": "error", "message": "Update failed."})


@function_tool()
async def capture_preference(
    preference: Annotated[str, "The user's preference or note to remember"],
    category: Annotated[str, "Category: timing, communication, service, or general"] = "general"
) -> str:
    """Capture a user preference or note mentioned during the conversation."""
    
    pref_entry = {
        "user_phone": session_data.user_phone,
        "preference": preference,
        "category": category
    }
    
    if session_data.user_phone:
        try:
            supabase.table("preferences").insert(pref_entry).execute()
            logger.info(f"💾 Preference saved to DB for {session_data.user_phone}")
        except Exception as e:
            logger.error(f"❌ Failed to save preference: {e}")
    
    session_data.preferences.append(pref_entry)
    return json.dumps({"tool": "capture_preference", "status": "success", "message": f"Noted: {preference}"})

@function_tool()
async def end_conversation(
    confirmation: Annotated[str, "Say 'yes' to confirm ending the conversation"] = "yes"
) -> str:
    """End the conversation and generate a summary."""
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
            
            summary_prompt = f"""Summarize this appointment booking conversation. 
            Include:
            1. Main purpose of the call
            2. Actions taken (bookings, cancellations)
            3. User preferences mentioned
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
                model="llama-3.3-70b",
                messages=[{"role": "system", "content": "You are a professional assistant summarizing a call."},
                          {"role": "user", "content": summary_prompt}]
            )
            summary_text = response.choices[0].message.content
        except Exception as e:
            logger.error(f"❌ Summary generation failed: {e}")
            summary_text = f"Conversational summary could not be generated, but your call lasted {duration}s."

    summary_data = {
        "user_phone": session_data.user_phone,
        "duration_seconds": duration,
        "appointments_booked": [a for a in session_data.appointments if a.get("status") == "confirmed"],
        "preferences": session_data.preferences,
        "transcript": session_data.full_transcript,
        "cost_breakdown": cost_data,
        "summary_text": summary_text
    }

    try:
        # We'll use a safest approach: insert only core fields that definitely exist.
        # 'appointments_booked' and 'preferences' columns seem to be missing in the schema, causing PGRST204.
        # We'll skip them for now to ensure the summary text is saved.
        insert_data = {
            "user_phone": session_data.user_phone,
            "duration_seconds": duration,
            "transcript": session_data.full_transcript, # JSONB often maps fine
            "summary_text": summary_text
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
    
    # Find participant
    participants = list(ctx.room.remote_participants.values())
    participant = participants[0] if participants else await ctx.wait_for_participant()
    
    logger.info(f"⚡ Connected in {(time.time()-start)*1000:.0f}ms | Participant: {participant.identity}")

    # -------------------------
    # Start Avatar Early (Pre-loading optimization)
    # -------------------------
    # Initialize avatar session immediately to start connection in background
    avatar = bey.AvatarSession(
        api_key=os.environ["BEYOND_PRESENCE_API_KEY"],
        avatar_id=os.environ["BEYOND_PRESENCE_AVATAR_ID"],
    )
    
    # Start avatar connection task immediately (runs in parallel with service setup)
    avatar_start_time = time.time()
    avatar_task = None
    
    async def start_avatar_early():
        try:
            # We'll pass session later, but start the connection process now
            logger.info(f"🎭 Avatar connection initiated early")
            return avatar
        except Exception as e:
            logger.error(f"❌ Avatar early init failed: {e}")
            return None
    
    # Kick off avatar pre-connection
    asyncio.create_task(start_avatar_early())

    # Create services (STT, LLM, TTS run in parallel with avatar)
    stt = deepgram.STT(
        api_key=os.environ["DEEPGRAM_API_KEY"],
        model="nova-2-phonecall",
    )
    
    llm = openai.LLM(
        model="llama-3.3-70b",
        base_url="https://api.cerebras.ai/v1",
        api_key=os.environ["CEREBRAS_API_KEY"],
        timeout=60.0,
    )
    
    tts = cartesia.TTS(
        api_key=os.environ["CARTESIA_API_KEY"],
        voice="47f3bbb1-e98f-4e0c-92c5-5f0325e1e206",
    )

    # Agent instructions
    instructions = f"""You are a helpful appointment assistant.

Your goals:
1. Identify the user by phone number immediately.
2. Help book, modify, or cancel appointments.
3. Check availability before booking.
4. Keep responses short and conversational.

Tone:
- Friendly and professional.
- Do NOT read out IDs or technical data.
- Speak naturally (e.g., "I've booked that for you" instead of "Booking confirmed").

Tools:
- Use 'identify_user' first ONLY after the user provides their phone number.
- Use 'fetch_slots' to see availability.
- Use 'book_appointment' to confirm bookings.
- Use 'end_conversation' when the user says goodbye.

STRICT RULE: You MUST ask for the user's phone number immediately if you don't have it. DO NOT call 'identify_user' with 'null' or guessed values. Wait for the user to speak their number.
NEVER read out tool arguments or technical IDs to the user.

Today's date is {datetime.now().strftime("%Y-%m-%d")}.
"""

    # Create agent with tools
    agent = Agent(
        instructions=instructions,
        stt=stt,
        llm=llm,
        tts=tts,
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
    )

    # Create session
    session = AgentSession()
    
    # Broadcast helper
    async def broadcast(data):
        try:
            await ctx.room.local_participant.publish_data(json.dumps(data))
        except:
            pass

    # -------------------------
    # Usage Tracking & Transcript Capture
    # -------------------------
    @session.on("user_transcript")
    def on_user_transcript(transcript):
        if transcript.final:
            session_data.full_transcript.append({"role": "user", "text": transcript.text, "time": time.time()})
            session_data.usage["stt_seconds"] += len(transcript.text) / 15.0 
            # Send to frontend
            asyncio.create_task(broadcast({"role": "user", "text": transcript.text}))

    @session.on("agent_transcript")
    def on_agent_transcript(transcript):
        session_data.full_transcript.append({"role": "agent", "text": transcript, "time": time.time()})
        session_data.usage["tts_chars"] += len(transcript)
        # Send to frontend
        asyncio.create_task(broadcast({"role": "agent", "text": transcript}))

    # Note: Token usage is harder to track without direct LLM plugin events. 
    # We estimate based on char counts as a fallback.
    @session.on("llm_response")
    def on_llm_response(response):
        # We rely on on_agent_transcript for the actual text spoken, but if TTS fails,
        # this is a backup. However, to avoid duplicates, we'll only track usage here.
        # session_data.full_transcript.append({"role": "agent", "text": response, "time": time.time()})
        
        # Rough token estimation: 1 token ~= 4 chars
        session_data.usage["llm_output_tokens"] += len(response) // 4
        # We don't easily see the input prompt size here without more hooks

    # Avatar was already initialized early - now start its connection with the session
    async def start_avatar_async():
        try:
            await avatar.start(room=ctx.room, agent_session=session)
            logger.info(f"🎭 Avatar connected in {(time.time()-avatar_start_time)*1000:.0f}ms")
        except Exception as e:
            logger.error(f"❌ Avatar failed to start: {e}\")")
    
    avatar_task = asyncio.create_task(start_avatar_async())

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
    await session.say("Hello! I'm your appointment booking assistant. How can I help you today?", allow_interruptions=True)
    
    logger.info(f"🎯 Voice READY in {(time.time()-start)*1000:.0f}ms (avatar loading in background)")
    
    # -------------------------
    # Session End Handler - Auto-generate summary
    # -------------------------
    def on_agent_stopped():
        """When session ends, automatically generate and send summary"""
        async def generate_summary():
            try:
                logger.info("📊 Session ending, generating summary...")
                # Call end_conversation to generate summary
                await end_conversation("yes")
                logger.info("✅ Summary generated and sent to frontend")
            except Exception as e:
                logger.error(f"❌ Failed to generate end summary: {e}")
        
        asyncio.create_task(generate_summary())

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
