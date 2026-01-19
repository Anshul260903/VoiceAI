import { useCallback, useState, useEffect } from "react";
import {
  LiveKitRoom,
  RoomAudioRenderer,
  useVoiceAssistant,
  VideoTrack,
  useRemoteParticipants,
  useDataChannel,
  useRoomContext,
} from "@livekit/components-react";
import { Track, RoomEvent } from "livekit-client";
import "@livekit/components-styles";

// Tool icons mapping
const TOOL_ICONS = {
  identify_user: "👤",
  fetch_slots: "📅",
  book_appointment: "✅",
  retrieve_appointments: "📋",
  cancel_appointment: "❌",
  modify_appointment: "✏️",
  capture_preference: "💡",
  end_conversation: "👋",
};

const TOOL_LABELS = {
  identify_user: "User Identified",
  fetch_slots: "Fetching Slots",
  book_appointment: "Booking Appointment",
  retrieve_appointments: "Loading Appointments",
  cancel_appointment: "Cancelling",
  modify_appointment: "Modifying",
  capture_preference: "Preference Noted",
  end_conversation: "Ending Session",
};

export default function App() {
  const [url, setURL] = useState(
    import.meta.env.VITE_LIVEKIT_URL || "wss://voice-ai-eon55hwv.livekit.cloud"
  );
  const [token, setToken] = useState("");
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const [summary, setSummary] = useState(null);

  // Backend URL from environment variable (for Vercel deployment)
  const rawURL = import.meta.env.VITE_SERVER_URL || "";
  const SERVER_URL = rawURL && !rawURL.startsWith("http") ? `https://${rawURL}` : rawURL;

  const startSession = async () => {
    try {
      setError("");
      setSummary(null);
      const roomName = `room-${Date.now()}`;
      const resp = await fetch(
        `${SERVER_URL}/getToken?roomName=${roomName}&identity=user-${Math.floor(
          Math.random() * 1000
        )}`
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

  const stopSession = (summaryData = null) => {
    if (summaryData) {
      setSummary(summaryData);
    }
    setToken("");
    setRunning(false);
  };

  return (
    <div className="container">
      <header>
        <h1>Voice AI</h1>
        <p>Appointment Booking Assistant</p>
      </header>

      {summary ? (
        <SummaryView summary={summary} onClose={() => setSummary(null)} onNewSession={startSession} />
      ) : !running ? (
        <div className="card">
          <div className="controls-center">
            <button id="micBtn" onClick={startSession}>
              <span id="micIcon">🎤</span>
              <span id="micText">Start Session</span>
            </button>
          </div>
          {error && <div className="error-msg">{error}</div>}
        </div>
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
          <SessionView onStop={stopSession} />
        </LiveKitRoom>
      )}
    </div>
  );
}

function SessionView({ onStop }) {
  const { state, audioTrack } = useVoiceAssistant();
  const room = useRoomContext();
  const [toolCalls, setToolCalls] = useState([]);
  const [userInfo, setUserInfo] = useState(null);
  const [appointments, setAppointments] = useState([]);
  const [preferences, setPreferences] = useState([]);
  const [transcripts, setTranscripts] = useState([]);
  const [sessionStart] = useState(Date.now());
  const [waitingForSummary, setWaitingForSummary] = useState(false);

  // Capture actual transcripts from room events
  useEffect(() => {
    if (!room) return;

    const handleTranscription = (transcription, participant) => {
      // Find the speaker
      const speaker = participant?.isAgent ? 'agent' : 'user';

      // Get the text from segments (transcription is an object with a segments array)
      const text = transcription.segments.map(s => s.text).join(' ').trim();
      if (!text) return;

      // Only add final segments if possible, but LiveKit component chunks are usually good
      // For simplicity, we append each chunk. In a real app we'd merge by ID.
      setTranscripts(prev => {
        // Check if last message was same speaker and very recent to merge?
        // Or just append
        return [...prev.slice(-49), {
          speaker,
          text,
          time: new Date().toLocaleTimeString()
        }];
      });
    };

    // Also keep the tool result listener via data channel
    const handleData = (payload, participant) => {
      const decoder = new TextDecoder();
      const str = decoder.decode(payload);
      try {
        const data = JSON.parse(str);

        // Capture transcript broadcasts from agent (since LiveKit transcription might be flaky)
        if (data.role && data.text && !data.tool) {
          setTranscripts(prev => {
            // Deduplicate: if last message same as this, ignore
            const last = prev[prev.length - 1];
            if (last && last.text === data.text && last.speaker === data.role) return prev;

            return [...prev.slice(-49), {
              speaker: data.role,
              text: data.text,
              time: new Date().toLocaleTimeString()
            }];
          });
        }

        // Handle tool results
        if (data.tool) {
          setToolCalls(prev => [...prev.slice(-9), {
            tool: data.tool,
            status: data.status,
            message: data.message,
            timestamp: new Date().toLocaleTimeString()
          }]);

          if (data.tool === "identify_user" && data.status === "success") {
            setUserInfo({ phone: data.phone_number, name: data.name });
          }

          if (data.tool === "retrieve_appointments" && data.status === "success") {
            setAppointments(data.data);
          }

          if (data.tool === "book_appointment" && data.status === "success") {
            // Refresh list if needed, or just append
            setAppointments(prev => [...prev, data.data]);
          }
          if (data.tool === "end_conversation" && data.status === "success") {
            setWaitingForSummary(false);
            if (window.summaryFallbackTimer) clearTimeout(window.summaryFallbackTimer);
            onStop(data); // Use agent's complete summary
          }
        }
      } catch (e) { }
    };

    room.on(RoomEvent.TranscriptionReceived, handleTranscription);
    room.on(RoomEvent.DataReceived, handleData);

    return () => {
      room.off(RoomEvent.TranscriptionReceived, handleTranscription);
      room.off(RoomEvent.DataReceived, handleData);
    };
  }, [room, onStop]);

  // Generate comprehensive summary and stop session
  const handleEndSession = () => {
    // Stop the session immediately
    console.log('⏹ Ending session manually...');

    // Package current local state as a preliminary summary
    const duration = Math.floor((Date.now() - sessionStart) / 1000);
    const preliminarySummary = {
      tool: "end_conversation",
      status: "success",
      data: {
        user: userInfo || { phone: null, name: null },
        session: {
          duration_seconds: duration,
          timestamp: new Date().toISOString(),
          start_time: new Date(sessionStart).toLocaleTimeString(),
          end_time: new Date().toLocaleTimeString()
        },
        appointments_booked: appointments.filter(a => a.status === "confirmed"),
        preferences: preferences,
        transcript: transcripts,
        pendingSummary: true // Flag to show "Generate AI Summary" button
      },
      message: "Session ended"
    };

    // Clean up and notify parent
    onStop(preliminarySummary);
  };


  // Handle tool call results from agent transcripts
  const handleToolResult = useCallback((result) => {
    try {
      const data = JSON.parse(result);
      if (data.tool) {
        // Add to tool calls list
        setToolCalls(prev => [...prev.slice(-4), { ...data, timestamp: Date.now() }]);

        // Handle specific tools
        if (data.tool === "identify_user" && data.status === "success") {
          setUserInfo(data.data);
        }
        if (data.tool === "book_appointment" && data.status === "success") {
          setAppointments(prev => [...prev, data.data]);
        }
        if (data.tool === "cancel_appointment" && data.status === "success") {
          setAppointments(prev =>
            prev.map(a => a.id === data.data.id ? { ...a, status: "cancelled" } : a)
          );
        }
        if (data.tool === "retrieve_appointments" && data.status === "success") {
          setAppointments(data.data.appointments || []);
        }
        if (data.tool === "capture_preference" && data.status === "success") {
          setPreferences(prev => [...prev, data.data]);
        }
        if (data.tool === "end_conversation" && data.status === "success") {
          // Clear the fallback timer if it exists
          if (window.summaryFallbackTimer) {
            clearTimeout(window.summaryFallbackTimer);
            window.summaryFallbackTimer = null;
          }
          setWaitingForSummary(false);
          console.log('✅ Received agent summary with transcripts');
          onStop(data);
        }
      }
    } catch (e) {
      // Not a JSON tool result, ignore
    }
  }, [onStop]);

  return (
    <>
      <div className="card">
        <div className="controls-grid">
          <div className="btn-group">
            <button id="micBtn" className="danger" onClick={handleEndSession} disabled={waitingForSummary}>
              <span id="micIcon">{waitingForSummary ? "⏳" : "⏹"}</span>
              <span id="micText">{waitingForSummary ? "Generating Summary..." : "End Session"}</span>
            </button>
          </div>
          <div className="status-indicator">
            <div className={`pulse-dot ${state === 'speaking' ? 'active' : ''}`}></div>
            <span>Agent: {state.charAt(0).toUpperCase() + state.slice(1)}</span>
          </div>
        </div>

        {/* User Info Badge */}
        {userInfo && (
          <div className="user-badge">
            <span>👤</span>
            <span>{userInfo.name || userInfo.phone}</span>
          </div>
        )}

        {/* Avatar Video Display */}
        <AvatarDisplay />

        {/* Tool Calls Display */}
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

      {/* Appointments Panel */}
      {appointments.length > 0 && (
        <div className="card">
          <div className="panel-header">
            <div className="panel-title">📅 Appointments</div>
          </div>
          <div className="appointments-list">
            {appointments.map((apt, i) => (
              <div key={i} className={`appointment-item ${apt.status}`}>
                <div className="apt-date">{apt.date} at {apt.time}</div>
                <div className="apt-purpose">{apt.purpose}</div>
                <div className={`apt-status ${apt.status}`}>
                  {apt.status === "confirmed" ? "✓ Confirmed" : "✗ Cancelled"}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="card">
        <div className="panel-header">
          <div className="panel-title">Live Conversation</div>
        </div>
        <div id="live" className="panel">
          {state === 'listening' && "🎤 Listening..."}
          {state === 'thinking' && "🤔 Processing..."}
          {state === 'speaking' && "🗣️ Speaking..."}
          {state === 'idle' && "Say 'Hello' to start!"}
          {state === 'connecting' && "🔄 Connecting..."}
        </div>
      </div>

      <RoomAudioRenderer />
    </>
  );
}

// Summary view after conversation ends
function SummaryView({ summary, onClose, onNewSession }) {
  const [data, setData] = useState(summary.data || {});
  const [loadingAI, setLoadingAI] = useState(false);

  const appointments = data.appointments_booked || [];
  const cancelled = data.appointments_cancelled || [];
  const user = data.user || {};
  const session = data.session || {};
  const toolCalls = data.tool_calls || [];
  const transcripts = data.transcript || data.transcripts || []; // Handle both formats
  const summaryText = data.summary_text;
  const cost = data.cost_breakdown;

  const generateAISummary = async () => {
    setLoadingAI(true);
    const rawURL = import.meta.env.VITE_SERVER_URL || "";
    const SERVER_URL = rawURL && !rawURL.startsWith("http") ? `https://${rawURL}` : rawURL;
    try {
      const resp = await fetch(`${SERVER_URL}/generateSummary`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ transcript: transcripts })
      });
      const result = await resp.json();
      if (result.summary_text) {
        setData(prev => ({ ...prev, summary_text: result.summary_text, pendingSummary: false }));
      }
    } catch (e) {
      console.error("AI Summary Error:", e);
    } finally {
      setLoadingAI(false);
    }
  };

  return (
    <div className="card summary-card">
      <div className="summary-header">
        <h2>📋 Session Summary</h2>
        <div className="summary-meta">
          <span className="summary-time">
            Duration: {Math.floor(session.duration_seconds / 60)}m {session.duration_seconds % 60}s
          </span>
          {session.start_time && (
            <span className="summary-timestamp">
              {session.start_time} - {session.end_time}
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

      {/* AI Generated Summary */}
      {summaryText ? (
        <div className="summary-section ai-summary">
          <h3>✨ AI Summary</h3>
          <p className="summary-text-content">{summaryText}</p>
        </div>
      ) : data.pendingSummary && (
        <div className="summary-section ai-summary-trigger">
          <button className="generate-ai-btn" onClick={generateAISummary} disabled={loadingAI}>
            {loadingAI ? "⏳ Analyzing conversation..." : "✨ Generate AI Summary"}
          </button>
        </div>
      )}

      {/* Cost Breakdown */}
      {cost && (
        <div className="summary-section">
          <h3>💰 Cost Breakdown</h3>
          <div className="cost-grid">
            <div className="cost-item">
              <span className="cost-label">STT (Deepgram)</span>
              <span className="cost-val">{cost.stt.usage} | ${cost.stt.cost.toFixed(4)}</span>
            </div>
            <div className="cost-item">
              <span className="cost-label">TTS (Cartesia)</span>
              <span className="cost-val">{cost.tts.usage} | ${cost.tts.cost.toFixed(4)}</span>
            </div>
            <div className="cost-item">
              <span className="cost-label">LLM (Cerebras)</span>
              <span className="cost-val">{cost.llm.usage} | ${cost.llm.cost.toFixed(4)}</span>
            </div>
            <div className="cost-total">
              <strong>Total Cost: ${cost.total.toFixed(4)}</strong>
            </div>
          </div>
        </div>
      )}

      {/* Conversation Transcript */}
      {transcripts.length > 0 && (
        <div className="summary-section">
          <h3>💬 Conversation</h3>
          <div className="transcript-list">
            {transcripts.slice(-50).map((t, i) => (
              <div key={i} className={`transcript-item ${t.speaker || t.role}`}>
                <span className="transcript-speaker">{(t.speaker === 'user' || t.role === 'user') ? 'You' : 'Agent'}:</span>
                <span className="transcript-text">{t.text}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Actions Taken (Tool Calls) */}
      {toolCalls.length > 0 && (
        <div className="summary-section">
          <h3>🔧 Actions Taken</h3>
          <div className="actions-list">
            {toolCalls.map((tc, i) => (
              <div key={i} className={`action-item ${tc.status}`}>
                <span className="action-icon">{TOOL_ICONS[tc.tool] || '⚙️'}</span>
                <span className="action-name">{TOOL_LABELS[tc.tool] || tc.tool}</span>
                <span className={`action-status ${tc.status}`}>
                  {tc.status === 'success' ? '✓' : '✗'}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Appointments Booked */}
      {appointments.length > 0 && (
        <div className="summary-section">
          <h3>✅ Appointments Booked</h3>
          {appointments.map((apt, i) => (
            <div key={i} className="summary-apt">
              <strong>{apt.date}</strong> at <strong>{apt.time}</strong>
              <br />
              <span className="apt-purpose">{apt.purpose}</span>
            </div>
          ))}
        </div>
      )}

      {/* Appointments Cancelled */}
      {cancelled.length > 0 && (
        <div className="summary-section">
          <h3>❌ Appointments Cancelled</h3>
          {cancelled.map((apt, i) => (
            <div key={i} className="summary-apt cancelled">
              {apt.date} at {apt.time}
            </div>
          ))}
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

      {/* No activity message */}
      {appointments.length === 0 && cancelled.length === 0 && transcripts.length === 0 && (
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

// Avatar video display component
function AvatarDisplay() {
  const participants = useRemoteParticipants();

  const avatarParticipant = participants.find(p =>
    p.videoTrackPublications.size > 0
  );

  const videoTrack = avatarParticipant?.videoTrackPublications.values().next().value?.track;

  if (!videoTrack) {
    return (
      <div className="avatar-container avatar-loading">
        <div className="avatar-placeholder">
          <span>🎭</span>
          <p>Loading Avatar...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="avatar-container">
      <VideoTrack trackRef={{ participant: avatarParticipant, source: Track.Source.Camera }} />
    </div>
  );
}
