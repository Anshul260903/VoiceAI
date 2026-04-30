import { useCallback, useState, useEffect, useRef } from "react";
import {
  LiveKitRoom,
  RoomAudioRenderer,
  useVoiceAssistant,
  useRoomContext,
  useTracks,
  ParticipantTile,
  GridLayout,
} from "@livekit/components-react";
import { RoomEvent, Track } from "livekit-client";
import "@livekit/components-styles";

const COST_STT_PER_MIN = Number(import.meta.env.VITE_COST_STT_PER_MINUTE_USD ?? "0.0058");
const COST_TTS_PER_CHAR = Number(import.meta.env.VITE_COST_TTS_PER_CHAR_USD ?? "0.00001");
const COST_LLM_IN_PER_TOKEN = Number(import.meta.env.VITE_COST_LLM_INPUT_PER_TOKEN_USD ?? "0.00000059");
const COST_LLM_OUT_PER_TOKEN = Number(import.meta.env.VITE_COST_LLM_OUTPUT_PER_TOKEN_USD ?? "0.00000079");

// Tool icons mapping
const TOOL_ICONS = {
  identify_user: "👤",
  fetch_slots: "📅",
  book_appointment: "✅",
  retrieve_appointments: "📂",
  cancel_appointment: "❌",
  modify_appointment: "🔄",
  capture_preference: "💡",
  end_conversation: "👋",
  search_knowledge_base: "🔍",
};

const TOOL_LABELS = {
  identify_user: "User Identified",
  fetch_slots: "Fetching Slots",
  book_appointment: "Booking Confirmed",
  retrieve_appointments: "Retrieving Appointments",
  cancel_appointment: "Appointment Cancelled",
  modify_appointment: "Appointment Modified",
  capture_preference: "Preference Noted",
  end_conversation: "Ending Session",
  search_knowledge_base: "Searching KB",
};

const KB_API_URL = import.meta.env.VITE_KB_API_URL ?? "http://localhost:8001";

// ===========================
// App Root
// ===========================
export default function App() {
  const [isAuthenticated, setIsAuthenticated] = useState(() => {
    return localStorage.getItem("is_auth") === "true";
  });
  const [url, setURL] = useState(
    import.meta.env.VITE_LIVEKIT_URL || "wss://voice-ai-eon55hwv.livekit.cloud"
  );
  const [token, setToken] = useState("");
  const [roomName, setRoomName] = useState("");
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const [summary, setSummary] = useState(null);
  const [transcripts, setTranscripts] = useState([]);
  const transcriptsRef = useRef([]);
  const [sessionStart, setSessionStart] = useState(null);
  const sessionEndedRef = useRef(false);

  // System Prompt state
  const DEFAULT_PROMPT = `You are a healthcare front-desk AI voice assistant.
Identify users by phone number, collect name, and help with appointments.
Use tools to fetch slots, book, retrieve, cancel, and modify appointments.
Working hours are 10:00 to 17:00, slot length is 45 minutes.
No cancellation or modification is allowed within 1 hour of appointment time.
Always confirm date, time, and intent before final booking.
Keep responses concise and conversational.`;

  const DEFAULT_GREETING = "Hello! I am your healthcare assistant. I can help you book, reschedule, cancel, or review appointments.";

  const [systemPrompt, setSystemPrompt] = useState(() => {
    return localStorage.getItem("system_prompt") || DEFAULT_PROMPT;
  });
  const [firstMessage, setFirstMessage] = useState(() => {
    return localStorage.getItem("first_message") || DEFAULT_GREETING;
  });
  const [promptExpanded, setPromptExpanded] = useState(false);

  // Knowledge Base state
  const [kbDocuments, setKbDocuments] = useState([]);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState("");
  const [dragOver, setDragOver] = useState(false);

  const rawURL = import.meta.env.VITE_SERVER_URL || "";
  const SERVER_URL = rawURL && !rawURL.startsWith("http") ? `https://${rawURL}` : rawURL;

  // Fetch KB documents on mount
  useEffect(() => {
    fetchKBDocuments();
  }, []);

  const fetchKBDocuments = async () => {
    try {
      const resp = await fetch(`${KB_API_URL}/api/kb/documents`);
      if (resp.ok) {
        const data = await resp.json();
        setKbDocuments(data.documents || []);
      }
    } catch (e) {
      console.log("KB API not available yet:", e.message);
    }
  };

  const handleSavePrompt = () => {
    localStorage.setItem("system_prompt", systemPrompt);
    localStorage.setItem("first_message", firstMessage);
    alert("✅ Agent persona saved! It will be remembered for next time.");
  };

  const handleResetPrompt = () => {
    if (confirm("Reset to default Sales Agent persona?")) {
      setSystemPrompt(DEFAULT_PROMPT);
      setFirstMessage(DEFAULT_GREETING);
      localStorage.removeItem("system_prompt");
      localStorage.removeItem("first_message");
    }
  };

  const handleFileUpload = async (files) => {
    if (!files || files.length === 0) return;
    setUploading(true);
    setUploadError("");

    for (const file of files) {
      try {
        const formData = new FormData();
        formData.append("file", file);
        const resp = await fetch(`${KB_API_URL}/api/kb/upload`, {
          method: "POST",
          body: formData,
        });
        const data = await resp.json();
        if (!resp.ok) {
          setUploadError(data.detail || "Upload failed");
        }
      } catch (e) {
        setUploadError(`Upload failed: ${e.message}. Is the KB API running on port 8001?`);
      }
    }

    await fetchKBDocuments();
    setUploading(false);
  };

  const handleFileDrop = (e) => {
    e.preventDefault();
    setDragOver(false);
    handleFileUpload(e.dataTransfer.files);
  };

  const handleDeleteDoc = async (docId) => {
    try {
      await fetch(`${KB_API_URL}/api/kb/documents/${docId}`, { method: "DELETE" });
      await fetchKBDocuments();
    } catch (e) {
      console.error("Delete failed:", e);
    }
  };

  const handleLogin = (success) => {
    if (success) {
      setIsAuthenticated(true);
      localStorage.setItem("is_auth", "true");
    }
  };

  if (!isAuthenticated) {
    return <LoginOnboard onLogin={() => handleLogin(true)} />;
  }

  const startSession = async () => {
    try {
      setError("");
      setSummary(null);
      setTranscripts([]);
      transcriptsRef.current = [];
      sessionEndedRef.current = false;
      setSessionStart(Date.now());
      const roomName = `room-${Date.now()}`;
      setRoomName(roomName);
      const promptParam = systemPrompt ? `&systemPrompt=${encodeURIComponent(systemPrompt)}` : "";
      const greetingParam = firstMessage ? `&firstMessage=${encodeURIComponent(firstMessage)}` : "";
      const resp = await fetch(
        `${SERVER_URL}/getToken?roomName=${roomName}&identity=user-${Math.floor(
          Math.random() * 1000
        )}${promptParam}${greetingParam}`
      );
      const data = await resp.json().catch(() => ({}));

      if (!resp.ok) {
        const serverMsg = data?.error ? ` (${data.error})` : "";
        throw new Error(`Token request failed${serverMsg}`);
      }
      if (!data.token) {
        throw new Error("Token missing in response");
      }

      setToken(data.token);
      setRunning(true);
    } catch (e) {
      console.error(e);
      setError("Failed to connect: " + e.message);
    }
  };

  const handleTranscriptsUpdate = (updater) => {
    setTranscripts((prev) => {
      const next = typeof updater === "function" ? updater(prev) : updater;
      transcriptsRef.current = next;
      return next;
    });
  };

  const stopSession = (summaryData = null) => {
    if (sessionEndedRef.current && !summaryData) return;
    sessionEndedRef.current = true;

    if (summaryData) {
      setSummary(summaryData);
    } else if (transcriptsRef.current.length > 0) {
      const duration = sessionStart ? Math.floor((Date.now() - sessionStart) / 1000) : 0;
      setSummary({
        tool: "end_conversation",
        status: "success",
        data: {
          transcript: transcriptsRef.current,
          session: { duration_seconds: duration },
          user: {},
        },
        message: "Session ended",
      });
    }
    setToken("");
    setRunning(false);
  };

  return (
    <div className="container">


      {summary ? (
        <SummaryView summary={summary} onClose={() => setSummary(null)} onNewSession={startSession} />
      ) : !running ? (
        <div className="pre-call-dashboard">
          {/* System Prompt Editor */}
          <div className="card">
            <div className="section-header" onClick={() => setPromptExpanded(!promptExpanded)}>
              <div className="section-title">
                <span className="section-icon">📝</span>
                <span>System Prompt</span>
              </div>
              <button className="toggle-btn" type="button">
                {promptExpanded ? "▲ Collapse" : "▼ Expand"}
              </button>
            </div>
            {promptExpanded && (
              <div className="prompt-editor">
                <label className="field-label">First Message (Agent Greeting)</label>
                <input
                  className="greeting-input"
                  type="text"
                  value={firstMessage}
                  onChange={(e) => setFirstMessage(e.target.value)}
                  placeholder="Hello! How can I help you today?"
                />
                <label className="field-label" style={{ marginTop: 14 }}>System Instructions</label>
                <textarea
                  className="prompt-textarea"
                  value={systemPrompt}
                  onChange={(e) => setSystemPrompt(e.target.value)}
                  placeholder="Enter the agent's system prompt..."
                  rows={8}
                />
                <div className="prompt-footer">
                  <span className="char-count">{systemPrompt.length} characters</span>
                  <div className="prompt-actions">
                    <button
                      className="primary small-btn"
                      type="button"
                      onClick={handleSavePrompt}
                      style={{ marginRight: "8px" }}
                    >
                      💾 Save
                    </button>
                    <button
                      className="secondary small-btn"
                      type="button"
                      onClick={handleResetPrompt}
                    >
                      ↺ Reset Default
                    </button>
                  </div>
                </div>
              </div>
            )}
          </div>

          {/* Knowledge Base Upload */}
          <div className="card">
            <div className="section-header">
              <div className="section-title">
                <span className="section-icon">📚</span>
                <span>Knowledge Base</span>
              </div>
              <span className="doc-count">{kbDocuments.length} document{kbDocuments.length !== 1 ? "s" : ""}</span>
            </div>

            <div
              className={`upload-area ${dragOver ? "drag-over" : ""}`}
              onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
              onDragLeave={() => setDragOver(false)}
              onDrop={handleFileDrop}
              onClick={() => document.getElementById("kb-file-input").click()}
            >
              <input
                id="kb-file-input"
                type="file"
                accept=".pdf,.txt,.docx,.md"
                multiple
                style={{ display: "none" }}
                onChange={(e) => handleFileUpload(e.target.files)}
              />
              {uploading ? (
                <div className="upload-progress">
                  <div className="spinner" />
                  <span>Uploading & indexing...</span>
                </div>
              ) : (
                <>
                  <span className="upload-icon">📄</span>
                  <span className="upload-text">Drop files here or click to upload</span>
                  <span className="upload-hint">PDF, TXT, DOCX, MD supported</span>
                </>
              )}
            </div>

            {uploadError && <div className="error-msg">{uploadError}</div>}

            {kbDocuments.length > 0 && (
              <div className="doc-list">
                {kbDocuments.map((doc) => (
                  <div key={doc.id} className="doc-item">
                    <div className="doc-info">
                      <span className="doc-icon">
                        {doc.file_type === "pdf" ? "📕" : doc.file_type === "docx" ? "📘" : "📄"}
                      </span>
                      <div className="doc-details">
                        <span className="doc-name">{doc.filename}</span>
                        <span className="doc-meta">
                          {doc.num_chunks} chunks • {(doc.text_length / 1000).toFixed(1)}k chars
                        </span>
                      </div>
                    </div>
                    <button
                      className="doc-delete-btn"
                      onClick={(e) => { e.stopPropagation(); handleDeleteDoc(doc.id); }}
                      title="Remove document"
                    >
                      ✕
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Start Session */}
          <div className="card">
            <div className="controls-center">
              <button id="startBtn" className="start-btn" onClick={startSession}>
                <span className="btn-icon">🎤</span>
                <span>Start Session</span>
              </button>
              <span className="start-subtitle">Tap to begin your voice conversation</span>

            </div>
            {error && <div className="error-msg">{error}</div>}
          </div>
        </div >
      ) : (
        <LiveKitRoom
          serverUrl={url}
          token={token}
          connect={true}
          audio={true}
          video={false}
          onDisconnected={() => stopSession()}
          className="lk-room-container"
        >
          <SessionView
            onStop={stopSession}
            transcripts={transcripts}
            onTranscriptsUpdate={handleTranscriptsUpdate}
            sessionStart={sessionStart}
            roomName={roomName}
          />
        </LiveKitRoom>
      )}
    </div >
  );
}



// ===========================
// Call Timer
// ===========================
function CallTimer({ startTime }) {
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    const interval = setInterval(() => {
      setElapsed(Math.floor((Date.now() - startTime) / 1000));
    }, 1000);
    return () => clearInterval(interval);
  }, [startTime]);

  const mins = String(Math.floor(elapsed / 60)).padStart(2, "0");
  const secs = String(elapsed % 60).padStart(2, "0");

  return (
    <div className="call-timer">
      <div className="timer-dot" />
      <span>{mins}:{secs}</span>
    </div>
  );
}

// ===========================
// Session View (Active Call)
// ===========================
function SessionView({ onStop, transcripts, onTranscriptsUpdate, sessionStart, roomName }) {
  const { state } = useVoiceAssistant();
  const room = useRoomContext();
  const [toolCalls, setToolCalls] = useState([]);
  const [userInfo, setUserInfo] = useState(null);
  const [ending, setEnding] = useState(false);
  const [avatarError, setAvatarError] = useState("");
  const [avatarDebug, setAvatarDebug] = useState({
    participantIdentity: null,
    participantName: null,
    cameraTrackSid: null,
    micTrackSid: null,
    micSubscribed: false,
    cameraSubscribed: false,
    connectedParticipants: 0,
  });
  const avatarStartLockRef = useRef(false);
  const avatarRoomRef = useRef("");

  const [preferences, setPreferences] = useState([]);
  const [ragSources, setRagSources] = useState([]);
  const [ragExpanded, setRagExpanded] = useState(true);
  const chatEndRef = useRef(null);

  // Auto-scroll transcript chat
  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [transcripts]);

  const startAvatarSession = async () => {
    if (!roomName) return;
    if (avatarStartLockRef.current && avatarRoomRef.current === roomName) return;
    avatarStartLockRef.current = true;
    avatarRoomRef.current = roomName;
    const rawURL = import.meta.env.VITE_SERVER_URL || "";
    const SERVER_URL = rawURL && !rawURL.startsWith("http") ? `https://${rawURL}` : rawURL;
    try {
      const resp = await fetch(`${SERVER_URL}/startAvatar`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ roomName }),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        setAvatarError(err?.error || "Avatar failed to start");
      } else {
        setAvatarError("");
      }
    } catch (e) {
      setAvatarError("Avatar startup request failed");
    }
  };

  // Start Beyond Presence avatar session once room is active
  useEffect(() => {
    avatarStartLockRef.current = false;
    avatarRoomRef.current = roomName || "";
    startAvatarSession();
  }, [roomName]);

  const addTranscript = (entry) => {
    onTranscriptsUpdate((prev) => {
      const last = prev[prev.length - 1];
      if (last && last.text === entry.text && last.speaker === entry.speaker) return prev;
      return [...prev.slice(-49), entry];
    });
  };

  // Listen for data from agent
  useEffect(() => {
    if (!room) return;

    const handleData = (payload, participant) => {
      const decoder = new TextDecoder();
      const str = decoder.decode(payload);
      try {
        const data = JSON.parse(str);

        // Transcript broadcasts
        if (data.role && data.text && !data.tool) {
          addTranscript({
            speaker: data.role,
            text: data.text,
            time: new Date().toLocaleTimeString(),
          });
        }

        // Tool results
        if (data.tool) {
          setToolCalls((prev) => [
            ...prev.slice(-9),
            {
              tool: data.tool,
              status: data.status,
              message: data.message,
              timestamp: new Date().toLocaleTimeString(),
            },
          ]);

          if (data.tool === "identify_user" && data.status === "success") {
            setUserInfo({ phone: data.phone_number, name: data.name });
          }

          if (data.tool === "end_conversation" && data.status === "success") {
            onStop(data);
          }
        }

        // Handle RAG sources
        if (data.type === "rag_sources" && data.sources) {
          setRagSources(data.sources);
        }
      } catch (e) { }
    };

    room.on(RoomEvent.DataReceived, handleData);

    const refreshAvatarDebug = () => {
      try {
        const participants = Array.from(room.remoteParticipants.values());
        const avatarParticipant = participants.find((p) => {
          const id = p.identity || "";
          const nm = (p.name || "").toLowerCase();
          return id.startsWith("bey-avatar-") || nm.includes("beyond presence");
        });

        if (!avatarParticipant) {
          setAvatarDebug((prev) => ({
            ...prev,
            participantIdentity: null,
            participantName: null,
            cameraTrackSid: null,
            micTrackSid: null,
            micSubscribed: false,
            cameraSubscribed: false,
            connectedParticipants: participants.length,
          }));
          return;
        }

        const camPub = Array.from(avatarParticipant.trackPublications.values()).find(
          (pub) => pub.kind === "video" || pub.source === Track.Source.Camera
        );
        const micPub = Array.from(avatarParticipant.trackPublications.values()).find(
          (pub) => pub.kind === "audio" || pub.source === Track.Source.Microphone
        );

        const nextDebug = {
          participantIdentity: avatarParticipant.identity || null,
          participantName: avatarParticipant.name || null,
          cameraTrackSid: camPub?.trackSid || null,
          micTrackSid: micPub?.trackSid || null,
          micSubscribed: !!micPub?.isSubscribed,
          cameraSubscribed: !!camPub?.isSubscribed,
          connectedParticipants: participants.length,
        };
        setAvatarDebug(nextDebug);
        console.log("Avatar debug:", nextDebug);
      } catch (e) {
        console.warn("Avatar debug refresh failed:", e);
      }
    };

    refreshAvatarDebug();
    room.on(RoomEvent.ParticipantConnected, refreshAvatarDebug);
    room.on(RoomEvent.ParticipantDisconnected, refreshAvatarDebug);
    room.on(RoomEvent.TrackSubscribed, refreshAvatarDebug);
    room.on(RoomEvent.TrackUnsubscribed, refreshAvatarDebug);
    room.on(RoomEvent.TrackPublished, refreshAvatarDebug);
    room.on(RoomEvent.TrackUnpublished, refreshAvatarDebug);

    return () => {
      room.off(RoomEvent.DataReceived, handleData);
      room.off(RoomEvent.ParticipantConnected, refreshAvatarDebug);
      room.off(RoomEvent.ParticipantDisconnected, refreshAvatarDebug);
      room.off(RoomEvent.TrackSubscribed, refreshAvatarDebug);
      room.off(RoomEvent.TrackUnsubscribed, refreshAvatarDebug);
      room.off(RoomEvent.TrackPublished, refreshAvatarDebug);
      room.off(RoomEvent.TrackUnpublished, refreshAvatarDebug);
    };
  }, [room, onStop]);

  const handleEndSession = () => {
    if (ending) return;
    setEnding(true);
    const duration = Math.floor((Date.now() - sessionStart) / 1000);
    const summaryData = {
      tool: "end_conversation",
      status: "success",
      data: {
        user: userInfo || { phone: null, name: null },
        session: { duration_seconds: duration },

        transcript: transcripts,
        preferences: preferences,
        pendingSummary: transcripts.length > 0,
      },
      message: "Session ended",
    };

    try {
      const encoder = new TextEncoder();
      const payload = JSON.stringify({ action: "end_session" });
      room?.localParticipant?.publishData(encoder.encode(payload), { reliable: true });
    } catch (e) {
      console.error("Failed to send end signal:", e);
    }

    // Wait briefly for backend-generated end_conversation payload (includes cost breakdown).
    // Fallback to local summary only if backend payload is not received in time.
    setTimeout(() => {
      onStop(summaryData);
    }, 4500);
  };

  const stateLabel = {
    listening: "Listening...",
    thinking: "Processing...",
    speaking: "Speaking...",
    idle: "Ready",
    connecting: "Connecting...",
  };

  const stateIcon = {
    listening: "🎤",
    thinking: "🧠",
    speaking: "🗣️",
    idle: "⏸️",
    connecting: "🔄",
  };

  const cameraTracks = useTracks([{ source: Track.Source.Camera, withPlaceholder: false }]);
  const avatarTracks = cameraTracks.filter((t) => {
    const id = t.participant?.identity || "";
    const name = t.participant?.name || "";
    return id.startsWith("bey-avatar-") || name.toLowerCase().includes("beyond presence");
  });

  return (
    <div className="session-container">
      {/* Top Bar */}
      <div className="card">
        <div className="session-topbar">
          <div className="session-left">
            <button className="danger end-session-btn" onClick={handleEndSession} disabled={ending}>
              <span>⏹</span> {ending ? "Ending..." : "End Session"}
            </button>
            <CallTimer startTime={sessionStart} />
          </div>
          <div className={`agent-state-display ${state}`}>
            <div className={`state-dot ${state === "speaking" || state === "listening" ? "active" : ""}`} />
            <span>{stateIcon[state] || "⏸️"} {stateLabel[state] || state}</span>
          </div>
        </div>

        {userInfo && (
          <div className="user-badge">
            <span>👤</span>
            <span>{userInfo.name || userInfo.phone}</span>
          </div>
        )}

        {toolCalls.length > 0 && (
          <div className="tool-calls">
            {toolCalls.map((tool, i) => (
              <div key={i} className={`tool-call ${tool.status}`}>
                <span className="tool-icon">{TOOL_ICONS[tool.tool] || "🔧"}</span>
                <span className="tool-name">{TOOL_LABELS[tool.tool] || tool.tool}</span>
                {tool.status === "success" && <span className="tool-status">✓</span>}
                {tool.status === "error" && <span className="tool-status error">!</span>}
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="card">
        <div className="panel-title">👤 Live Avatar</div>
        {avatarError ? (
          <div className="error-msg">{avatarError}</div>
        ) : (
          <div style={{ minHeight: 220 }}>
            {avatarTracks.length > 0 ? (
              <GridLayout tracks={avatarTracks.slice(0, 1)}>
                <ParticipantTile />
              </GridLayout>
            ) : (
              <div className="error-msg">Waiting for Beyond Presence avatar video...</div>
            )}
          </div>
        )}
        <div style={{ marginTop: 10, fontSize: 12, opacity: 0.85 }}>
          <div>Participants: {avatarDebug.connectedParticipants}</div>
          <div>Avatar ID: {avatarDebug.participantIdentity || "not joined"}</div>
          <div>Avatar Name: {avatarDebug.participantName || "n/a"}</div>
          <div>Camera Track: {avatarDebug.cameraTrackSid || "none"} ({avatarDebug.cameraSubscribed ? "subscribed" : "not subscribed"})</div>
          <div>Mic Track: {avatarDebug.micTrackSid || "none"} ({avatarDebug.micSubscribed ? "subscribed" : "not subscribed"})</div>
        </div>
      </div>





      {/* RAG Sources Panel */}
      {ragSources.length > 0 && (
        <div className="card">
          <div className="panel-header" onClick={() => setRagExpanded(!ragExpanded)} style={{ cursor: "pointer" }}>
            <div className="panel-title">📚 RAG Sources Used</div>
            <span className="toggle-indicator">{ragExpanded ? "▲" : "▼"}</span>
          </div>
          {ragExpanded && (
            <div className="rag-sources-list">
              {ragSources.map((src, i) => (
                <div key={i} className="rag-source-item">
                  <div className="rag-source-header">
                    <span className="rag-doc-name">📄 {src.doc_name}</span>
                    <span className="rag-chunk-badge">Chunk {src.chunk_index + 1}</span>
                    <span className="rag-relevance" title="Relevance score">
                      {Math.round(src.relevance * 100)}% match
                    </span>
                  </div>
                  <div className="rag-source-snippet">{src.snippet}</div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      <RoomAudioRenderer />
    </div>
  );
}

// ===========================
// Summary View
// ===========================
function SummaryView({ summary, onClose, onNewSession }) {
  const [data, setData] = useState(summary.data || {});


  const user = data.user || {};
  const session = data.session || {};
  const toolCalls = data.tool_calls || [];
  const transcripts = data.transcript || data.transcripts || [];
  const buildFallbackSummary = () => {
    const userLines = transcripts.filter((t) => (t.speaker || t.role) === "user").map((t) => t.text).slice(0, 3);
    const agentLines = transcripts.filter((t) => (t.speaker || t.role) !== "user").map((t) => t.text).slice(0, 3);
    const minutes = Math.floor((session.duration_seconds || 0) / 60);
    const seconds = (session.duration_seconds || 0) % 60;
    const userPart = userLines.length ? `User asked about: ${userLines.join(" | ")}.` : "User interaction was captured.";
    const agentPart = agentLines.length ? `Assistant response included: ${agentLines.join(" | ")}.` : "Assistant response was limited.";
    return `${userPart} ${agentPart} Session duration was ${minutes}m ${seconds}s.`;
  };
  const summaryText = data.summary_text || buildFallbackSummary();
  const buildFallbackCost = () => {
    const userText = transcripts
      .filter((t) => (t.speaker || t.role) === "user")
      .map((t) => t.text || "")
      .join(" ");
    const agentText = transcripts
      .filter((t) => (t.speaker || t.role) !== "user")
      .map((t) => t.text || "")
      .join(" ");

    const sttSeconds = Math.max((userText.length || 0) / 15.0, session.duration_seconds || 0);
    const ttsChars = agentText.length || 0;
    const approxTotalTokens = Math.ceil((userText.length + agentText.length) / 4);
    const llmInputTokens = Math.ceil(approxTotalTokens * 0.65);
    const llmOutputTokens = Math.ceil(approxTotalTokens * 0.35);

    const sttCost = (sttSeconds / 60.0) * COST_STT_PER_MIN;
    const ttsCost = ttsChars * COST_TTS_PER_CHAR;
    const llmInputCost = llmInputTokens * COST_LLM_IN_PER_TOKEN;
    const llmOutputCost = llmOutputTokens * COST_LLM_OUT_PER_TOKEN;
    const llmCost = llmInputCost + llmOutputCost;
    const total = sttCost + ttsCost + llmCost;

    return {
      stt: { usage: `${sttSeconds.toFixed(1)}s`, cost: sttCost, rate: `$${COST_STT_PER_MIN.toFixed(4)}/min (est.)` },
      tts: { usage: `${ttsChars} chars`, cost: ttsCost, rate: `$${(COST_TTS_PER_CHAR * 1_000_000).toFixed(2)}/1M chars (est.)` },
      llm: {
        usage: `${llmInputTokens}in + ${llmOutputTokens}out tokens`,
        cost: llmCost,
        rate: `$${(COST_LLM_IN_PER_TOKEN * 1_000_000).toFixed(2)}/1M in, $${(COST_LLM_OUT_PER_TOKEN * 1_000_000).toFixed(2)}/1M out (est.)`,
      },
      total,
    };
  };
  const cost = data.cost_breakdown || buildFallbackCost();

  return (
    <div className="card summary-card">
      <div className="summary-header">
        <h2>📋 Session Summary</h2>
        <div className="summary-meta">
          <span className="summary-time">
            ⏱ {Math.floor((session.duration_seconds || 0) / 60)}m {(session.duration_seconds || 0) % 60}s
          </span>
          {session.start_time && (
            <span className="summary-timestamp">
              {session.start_time} — {session.end_time}
            </span>
          )}
        </div>
      </div>

      {/* User Info */}
      {user.phone && (
        <div className="summary-section">
          <h3>👤 User</h3>
          <p>{user.name || "Guest"} • {user.phone}</p>
        </div>
      )}

      {/* AI Summary */}
      <div className="summary-section ai-summary">
        <h3>✨ AI Summary</h3>
        <p className="summary-text-content">{summaryText}</p>
      </div>

      {/* Cost Breakdown */}
      {cost && (
        <div className="summary-section">
          <h3>💰 Cost Breakdown</h3>
          <div className="cost-summary-grid">
            <div className="cost-card">
              <div className="cost-header">STT</div>
              <div className="cost-body">
                <div className="cost-usage">{cost.stt.usage}</div>
                <div className="cost-amt">${cost.stt.cost.toFixed(4)}</div>
              </div>
              <div className="cost-footer">{cost.stt.rate}</div>
            </div>
            <div className="cost-card">
              <div className="cost-header">TTS</div>
              <div className="cost-body">
                <div className="cost-usage">{cost.tts.usage}</div>
                <div className="cost-amt">${cost.tts.cost.toFixed(4)}</div>
              </div>
              <div className="cost-footer">{cost.tts.rate}</div>
            </div>
            <div className="cost-cardHighlight">
              <div className="cost-header">LLM</div>
              <div className="cost-body">
                <div className="cost-usage">{cost.llm.usage}</div>
                <div className="cost-amt">${cost.llm.cost.toFixed(4)}</div>
              </div>
              <div className="cost-footer">{cost.llm.rate}</div>
            </div>
          </div>
          <div className="cost-total-banner">
            <span className="total-label">Estimated Session Cost:</span>
            <span className="total-value">${cost.total.toFixed(4)}</span>
          </div>
        </div>
      )}

      {/* Transcript */}
      {transcripts.length > 0 && (
        <div className="summary-section">
          <h3>💬 Conversation</h3>
          <div className="transcript-list">
            {transcripts.slice(-50).map((t, i) => (
              <div key={i} className={`transcript-item ${t.speaker || t.role}`}>
                <span className="transcript-speaker">
                  {t.speaker === "user" || t.role === "user" ? "You" : "Agent"}:
                </span>
                <span className="transcript-text">{t.text}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Tool Calls */}
      {toolCalls.length > 0 && (
        <div className="summary-section">
          <h3>🔧 Actions Taken</h3>
          <div className="actions-list">
            {toolCalls.map((tc, i) => (
              <div key={i} className={`action-item ${tc.status}`}>
                <span className="action-icon">{TOOL_ICONS[tc.tool] || "⚙️"}</span>
                <span className="action-name">{TOOL_LABELS[tc.tool] || tc.tool}</span>
                <span className={`action-status ${tc.status}`}>
                  {tc.status === "success" ? "✓" : "✗"}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}



      {/* Preferences */}
      {(data.preferences || []).length > 0 && (
        <div className="summary-section">
          <h3>💡 Your Preferences</h3>
          <ul className="preferences-list">
            {data.preferences.map((pref, i) => (
              <li key={i}>{pref.preference}</li>
            ))}
          </ul>
        </div>
      )}

      {/* No activity */}
      {transcripts.length === 0 && (
        <div className="summary-section">
          <p className="no-appointments">No activity recorded in this session.</p>
        </div>
      )}

      <div className="summary-actions">
        <button onClick={onNewSession}>
          <span>🎤</span> New Session
        </button>
        <button className="secondary" onClick={onClose}>
          Close
        </button>
      </div>
    </div>
  );
}

// ===========================
// Login Page
// ===========================
function LoginOnboard({ onLogin }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const handleSubmit = (e) => {
    e.preventDefault();
    setLoading(true);
    setError("");

    setTimeout(() => {
      if (email === "anshul@test.in" && password === "test@anshul.in") {
        onLogin();
      } else {
        setError("Invalid email or password. Please try again.");
      }
      setLoading(false);
    }, 800);
  };

  return (
    <div className="login-screen">
      <div className="login-card">
        <div className="login-header">

          <p>Sign in to access your assistant</p>
        </div>

        {error && <div className="login-error">{error}</div>}

        <form className="login-form" onSubmit={handleSubmit}>
          <div className="form-group">
            <label>Email Address</label>
            <input
              type="email"
              className="form-input"
              placeholder="Enter your email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
            />
          </div>
          <div className="form-group">
            <label>Password</label>
            <input
              type="password"
              className="form-input"
              placeholder="Enter your password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </div>
          <button type="submit" className="login-btn" disabled={loading}>
            {loading ? "Authenticating..." : "Sign In"}
          </button>
        </form>

        <div className="form-footer">
          <p>Authorized access only</p>
        </div>
      </div>
    </div>
  );
}
