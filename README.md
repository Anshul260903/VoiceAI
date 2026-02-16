# 🎙️ Real-Time Voice AI with RAG

A full-stack, real-time voice AI assistant that enables natural voice conversations over WebRTC, augmented with Retrieval-Augmented Generation (RAG) from uploaded documents.

Users can upload documents, customize the agent's behavior via editable system prompts, and have real-time voice conversations where the agent intelligently retrieves and references uploaded content.

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Frontend (React + Vite)                    │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────────┐  │
│  │ Voice UI │  │ Doc      │  │ System   │  │ RAG Sources│  │
│  │ Controls │  │ Upload   │  │ Prompt   │  │ Panel      │  │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────────────┘  │
│       │              │             │                         │
│  ┌────┴──────────────┴─────────────┴──────────────────────┐  │
│  │              LiveKit Client SDK (WebRTC)               │  │
│  └────────────────────────┬───────────────────────────────┘  │
└───────────────────────────┼──────────────────────────────────┘
                            │ WebRTC (Audio) + Data Channel
┌───────────────────────────┼──────────────────────────────────┐
│                    LiveKit Cloud Server                       │
└───────────────────────────┼──────────────────────────────────┘
                            │
┌───────────────────────────┼──────────────────────────────────┐
│              Python Voice Agent (LiveKit Agents SDK)          │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────────┐  │
│  │ Deepgram │  │ Cerebras │  │ Cartesia │  │ KB Search  │  │
│  │ STT      │──│ LLM      │──│ TTS      │  │ Tool       │  │
│  └──────────┘  └──────────┘  └──────────┘  └─────┬──────┘  │
└────────────────────────────────────────────────────┼─────────┘
                                                     │ HTTP
┌────────────────────────────────────────────────────┼─────────┐
│                 Knowledge Base API (FastAPI)        │         │
│  ┌──────────┐  ┌──────────┐  ┌──────────────────┐  │         │
│  │ PDF/DOCX │  │ Text     │  │ ChromaDB         │◄─┘         │
│  │ Parser   │──│ Chunker  │──│ (Vector Store)   │            │
│  └──────────┘  └──────────┘  └──────────────────┘            │
└──────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────┐
│              Node.js Backend (Express)                        │
│  ┌──────────────────┐  ┌─────────────────────────┐           │
│  │ LiveKit Token     │  │ Room Metadata           │           │
│  │ Generation        │  │ (System Prompt, Greeting)│          │
│  └──────────────────┘  └─────────────────────────┘           │
└──────────────────────────────────────────────────────────────┘
```

### Tech Stack

| Component | Technology | Purpose |
|---|---|---|
| **Frontend** | React 18 + Vite | UI, voice controls, document upload |
| **Backend** | Node.js + Express | LiveKit token generation, room config |
| **Voice Agent** | Python + LiveKit Agents SDK v1.3 | Voice pipeline orchestration |
| **STT** | Deepgram (Nova-2 Phonecall) | Real-time speech-to-text |
| **LLM** | Groq (Llama 3.3 70B) | Ultra-fast inference (~300+ tok/s) |
| **TTS** | Cartesia | Natural voice synthesis |
| **Vector Store** | ChromaDB | Document embedding storage + retrieval |
| **KB API** | FastAPI + Uvicorn | Document ingestion + RAG query endpoint |
| **Real-time** | LiveKit Cloud (WebRTC) | Low-latency audio streaming |

---

## ✨ Features

### Core
- **Real-time voice conversation** over WebRTC via LiveKit
- **RAG during live calls** — agent retrieves relevant document context and speaks answers
- **Document upload** — PDF, DOCX, and TXT files parsed, chunked, and indexed in ChromaDB
- **Editable system prompt** — customize agent behavior from the UI
- **Editable first message** — set a custom agent greeting

### UI Features
- **Live transcript** — real-time conversation feed with speaker labels
- **RAG Sources panel** — shows which document chunks were retrieved (doc name, chunk#, relevance %)
- **Audio visualizer** — animated waveform during active call
- **Call timer** — live elapsed time display
- **Agent state indicator** — shows Listening / Thinking / Speaking states
- **Tool call indicators** — visual feedback when agent uses tools

### Agent Capabilities
- **Interruption handling** — user can interrupt the agent mid-speech
- **Knowledge base search** — `search_knowledge_base` tool for RAG retrieval
- **End conversation** — graceful session termination with `end_conversation` tool
- **Custom greetings** — uses the configured first message on session start

---

## 📋 Prerequisites

- **Node.js** ≥ 18
- **Python** ≥ 3.10
- **npm** (comes with Node.js)

### API Keys Required

| Service | Purpose | Get Key |
|---|---|---|
| LiveKit Cloud | WebRTC infrastructure | [livekit.io](https://cloud.livekit.io) |
| Deepgram | Speech-to-Text | [deepgram.com](https://console.deepgram.com) |
| Groq | LLM Inference | [groq.com](https://console.groq.com) |
| Cartesia | Text-to-Speech | [cartesia.ai](https://play.cartesia.ai) |

---

## 🚀 Setup

### 1. Clone the repository

```bash
git clone https://github.com/Anshul260903/VoiceAI.git
cd VoiceAI
```

### 2. Install frontend + backend dependencies

```bash
npm install
```

### 3. Set up the Python agent

```bash
cd agent
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cd ..
```

### 4. Configure environment variables

Create a `.env` file in the **project root**:

```env
# LiveKit
LIVEKIT_URL=wss://your-project.livekit.cloud
LIVEKIT_API_KEY=your_livekit_api_key
LIVEKIT_API_SECRET=your_livekit_api_secret

# Frontend
VITE_LIVEKIT_URL=wss://your-project.livekit.cloud
VITE_SERVER_URL=http://localhost:8080

# Server
PORT=8080

# Agent APIs
DEEPGRAM_API_KEY=your_deepgram_key
GROQ_API_KEY=your_groq_key
CARTESIA_API_KEY=your_cartesia_key
```

The agent reads from the **same `.env` file** (loaded via `python-dotenv`). No separate agent `.env` is needed when running locally.

---

## ▶️ Running the Application

### Option A: Single command (recommended)

From the project root, this starts all 3 services in parallel:

```bash
npm run dev
```

This runs:
| Service | Command | Port |
|---|---|---|
| Frontend (Vite) | `vite` | `http://localhost:5173` |
| Backend (Express) | `node server/server.js` | `http://localhost:8080` |
| KB API (FastAPI) | `python kb_api.py` | `http://localhost:8001` |

### Then, in a separate terminal, start the voice agent:

```bash
cd agent
source venv/bin/activate
python agent.py dev
```

The agent registers with LiveKit Cloud and waits for incoming voice sessions.

### Option B: Run services individually

```bash
# Terminal 1 — Frontend
npx vite

# Terminal 2 — Backend
node server/server.js

# Terminal 3 — KB API
cd agent && source venv/bin/activate && python kb_api.py

# Terminal 4 — Voice Agent
cd agent && source venv/bin/activate && python agent.py dev
```

---

## 🎯 Usage

1. **Open** `http://localhost:5173` in your browser
2. **Upload a document** — click the upload area and select a PDF, DOCX, or TXT file
3. **Customize the system prompt** (optional) — expand the "System Prompt" section and edit
4. **Set a custom greeting** (optional) — edit the "First Message" field
5. **Start a session** — click the "Start Session" button and allow microphone access
6. **Talk to the agent** — ask questions about your uploaded document
7. **View RAG sources** — when the agent retrieves from the KB, the sources panel appears
8. **End the session** — click "End Session" or say goodbye

---

## 📁 Project Structure

```
VoiceAI/
├── src/                        # Frontend (React)
│   ├── App.jsx                 # Main application component
│   ├── main.jsx                # React entry point
│   └── style.css               # All styles
│
├── server/                     # Node.js backend
│   ├── server.js               # Express server (static + API proxy)
│   └── livekit/
│       └── router.js           # LiveKit token generation endpoint
│
├── agent/                      # Python voice agent
│   ├── agent.py                # Main agent (STT → LLM → TTS + tools)
│   ├── kb_api.py               # Knowledge Base API (FastAPI + ChromaDB)
│   ├── requirements.txt        # Python dependencies
│   ├── Procfile                # Railway deployment config
│   └── venv/                   # Python virtual environment
│
├── index.html                  # Vite HTML entry point
├── vite.config.js              # Vite configuration
├── package.json                # Node.js dependencies + scripts
├── vercel.json                 # Vercel deployment config
└── .env                        # Environment variables (not committed)
```

---

## ⚙️ How It Works

### Voice Pipeline

```
User speaks → Deepgram STT → Groq LLM → Cartesia TTS → User hears
                                  ↑
                          search_knowledge_base()
                                  ↑
                          KB API → ChromaDB
```

1. **User speaks** into the microphone — audio is streamed via WebRTC to the LiveKit room
2. **Deepgram STT** transcribes the audio in real-time (streaming, Nova-2 Phonecall model)
3. **Groq LLM** (Llama 3.3 70B) processes the transcript and decides whether to:
   - Respond directly, or
   - Call `search_knowledge_base` to retrieve document context
4. If RAG is triggered, **ChromaDB** returns the top-3 most relevant chunks
5. The LLM generates a response using the retrieved context
6. **Cartesia TTS** converts the text response to natural speech
7. Audio is streamed back through LiveKit to the user's browser

### Document Ingestion

1. User uploads a file (PDF/DOCX/TXT) via the frontend
2. The file is sent to the KB API (`POST /api/kb/upload`)
3. The document is parsed and split into ~500-character chunks
4. Each chunk is embedded and stored in ChromaDB
5. During calls, the agent queries these chunks via `POST /api/kb/query`

---

## 📊 Performance

Measured latencies from live testing (Network: India, Model: Llama 3.3 70B via Groq):

| Metric | Average | Range |
|---|---|---|
| **Agent connection** | ~660ms | 650–720ms |
| **LLM TTFT** (Time to First Token) | ~380ms | 300–450ms |
| **LLM throughput** | ~340 tok/s | 300–400 tok/s |
| **TTS TTFB** | ~225ms | 206–300ms |
| **KB search (RAG)** | ~150ms | 138–165ms |
| **Total response time** (non-RAG) | ~0.6–0.9s | — |
| **Total response time** (with RAG) | ~1.2–1.5s | — |
---

## ⚠️ Known Limitations & Tradeoffs

1. **Cerebras tool-calling**: Cerebras can occasionally struggle with tool-call JSON generation. Mitigated by setting `parallel_tool_calls=False` and `temperature=0.7`.

2. **No persistent storage**: ChromaDB runs in-memory by default. Uploaded documents are lost when the KB API restarts. For production, configure ChromaDB with persistent storage.

3. **Single-user KB**: The knowledge base is shared across all sessions. There's no per-user document isolation.

4. **No authentication**: The app has a simple hardcoded login page but no real authentication system.

5. **Supabase DB not configured locally**: The agent has code to save call summaries to Supabase, but this is not configured in the local dev setup (logs a non-fatal error).

6. **`ctx.disconnect()` warning**: The LiveKit Agents SDK v1.3 changed the disconnect API. A non-fatal warning appears on session end — does not affect functionality.

7. **WebRTC requires HTTPS in production**: LiveKit handles this via their cloud infrastructure, but if self-hosting, you'd need TLS certificates.

---

## 🔧 LiveKit Setup

This project uses **LiveKit Cloud** (free tier). To set up:

1. Go to [cloud.livekit.io](https://cloud.livekit.io) and create a project
2. Copy your **WebSocket URL** (e.g., `wss://your-project.livekit.cloud`)
3. Generate **API Key** and **API Secret** from the project settings
4. Add these to your `.env` file as `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`
5. Set `VITE_LIVEKIT_URL` to the same WebSocket URL for the frontend

The voice agent connects to LiveKit Cloud automatically when started with `python agent.py dev`.

---

## 📄 License

MIT
