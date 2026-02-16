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
    
    llm = openai.LLM(
        model="gemini-flash-latest",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        api_key=os.environ.get("GOOGLE_API_KEY"),
        timeout=60.0,
        temperature=0.7,
        parallel_tool_calls=False,
    )
    
    tts = cartesia.TTS(
        api_key=os.environ["CARTESIA_API_KEY"],
        voice="6786ecbd-b414-479d-b4ce-d500f2961556",
    )

    # Agent instructions — use custom prompt if provided, otherwise default
    # -------------------------
    # Strict RAG Instructions (ALWAYS APPLIED)
    # -------------------------
    base_system_prompt = f"""You are a specialized Knowledge Base Voice Assistant.
    
CRITICAL INSTRUCTIONS:
1. You have NO internal knowledge. You can ONLY answer by searching the database via the `search_knowledge_base` tool.
2. For EVERY user query, you MUST call `search_knowledge_base`.
3. If the tool returns information, use it to answer the user's question.
4. If the tool returns "No relevant documents found" or if the answer is not in the tool output, you MUST say exactly:
   "I'm sorry, I don't have any information related to that in my documents."
5. Do NOT use your own training data. Do NOT hallucinate.

Today's date is {datetime.now().strftime("%Y-%m-%d")}.
"""

    if custom_prompt:
        # Prepend strict rules to custom prompt to prevent jailbreaking/hallucination
        instructions = f"{base_system_prompt}\n\n--- User Context / Persona ---\n{custom_prompt}"
    else:
        # Default behavior with standard tone
        instructions = base_system_prompt + """
Tone:
- Helpful, concise, and spoken-style.
- Natural conversation (but strictly grounded in the tool output).
"""

    # RAG retrieval tool — queries the KB API
    KB_API_URL = os.environ.get("KB_API_URL", "http://localhost:8001")

    @function_tool
    async def search_knowledge_base(
        query: Annotated[str, "The user's query or a keyword search based on it"],
    ) -> str:
        """MANDATORY: Call this tool for EVERY user message to retrieve the answer. You cannot answer without it."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{KB_API_URL}/api/kb/query",
                    json={"query": query, "top_k": 1},
                )
                data = resp.json()
                results = data.get("results", [])

                if not results:
                    return "No relevant documents found in the knowledge base."

                context_parts = []
                sources_for_ui = []
                for r in results:
                    source = r["metadata"]["doc_name"]
                    text = r["text"]
                    # Truncate text to save tokens
                    if len(text) > 800:
                        text = text[:800] + "..."
                    context_parts.append(f"[Source: {source}]\n{text}")
                    sources_for_ui.append({
                        "doc_name": source,
                        "snippet": text[:150] + ("..." if len(text) > 150 else ""),
                        "chunk_index": r["metadata"].get("chunk_index", 0),
                        "relevance": round(1 - r.get("distance", 0), 2),
                    })

                # Broadcast RAG sources to frontend
                await broadcast({
                    "type": "rag_sources",
                    "query": query,
                    "sources": sources_for_ui,
                })

                logger.info(f"🔍 KB search: '{query[:40]}...' → {len(results)} results")
                return "\n\n---\n\n".join(context_parts)
        except Exception as e:
            logger.error(f"❌ KB search failed: {e}")
            return f"Knowledge base search failed: {str(e)}"

    # Create agent with tools
    agent = Agent(
        instructions=instructions,
        stt=stt,
        llm=llm,
        tts=tts,
        tools=[
            end_conversation,
            search_knowledge_base,
        ],
    )

    # Create session
    session = AgentSession()
    
    # Broadcast helper
    async def broadcast(data):
        try:
            await ctx.room.local_participant.publish_data(json.dumps(data), reliable=True)
        except:
            pass

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
                        await ctx.disconnect()
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
