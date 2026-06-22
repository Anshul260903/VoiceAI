import dotenv from "dotenv";
dotenv.config();

import express from "express";
import { createServer } from "http";
import path from "path";
import { fileURLToPath } from "url";
import { createLivekitRouter } from "./livekit/router.js";
import { AccessToken } from "livekit-server-sdk";
import { WebSocketServer, WebSocket } from "ws";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

console.log("🚀 LiveKit Voice AI Server");
console.log("🔑 LiveKit credentials configured:", !!process.env.LIVEKIT_API_KEY);

const app = express();
const server = createServer(app);

// CORS middleware - Allow Vercel frontend to access Railway backend
app.use((req, res, next) => {
    res.header("Access-Control-Allow-Origin", "*"); // Allow all origins (or specify your Vercel URL)
    res.header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS");
    res.header("Access-Control-Allow-Headers", "Content-Type, Authorization");
    if (req.method === "OPTIONS") {
        return res.sendStatus(200);
    }
    next();
});

// Serve built frontend when available (vite build -> dist)
app.use(express.static(path.join(__dirname, "..", "dist")));
app.use(express.json({ limit: "5mb" }));
app.get("/health", (_, res) => res.send("ok"));

// LiveKit router is optional — skip it if its env vars aren't set (test bot doesn't need it)
try {
    app.use(createLivekitRouter());
} catch (e) {
    console.warn("⚠️ LiveKit router disabled:", e.message);
}

// Start Beyond Presence Speech-to-Video avatar in an existing LiveKit room
app.post("/startAvatar", async (req, res) => {
    const { roomName } = req.body || {};
    if (!roomName) {
        return res.status(400).json({ error: "roomName is required" });
    }

    const beyApiKey = process.env.BEY_API_KEY || process.env.BEYOND_PRESENCE_API_KEY;
    const avatarId = process.env.BEY_AVATAR_ID || process.env.BEYOND_PRESENCE_AVATAR_ID;
    const livekitUrl = process.env.LIVEKIT_URL || process.env.VITE_LIVEKIT_URL;
    const { LIVEKIT_API_KEY, LIVEKIT_API_SECRET } = process.env;

    if (!beyApiKey || !avatarId || !livekitUrl || !LIVEKIT_API_KEY || !LIVEKIT_API_SECRET) {
        return res.status(400).json({
            error: "Missing BEY_API_KEY/BEY_AVATAR_ID/LIVEKIT credentials in environment",
        });
    }

    try {
        const avatarIdentity = `bey-avatar-${Date.now()}`;
        const at = new AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET, {
            identity: avatarIdentity,
            name: "Beyond Presence Avatar",
        });
        at.addGrant({
            roomJoin: true,
            room: roomName,
            canPublish: true,
            canSubscribe: true,
            canPublishData: true,
            agent: true,
        });
        const token = await at.toJwt();

        const response = await fetch("https://api.bey.dev/v1/sessions", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "x-api-key": beyApiKey,
            },
            body: JSON.stringify({
                avatar_id: avatarId,
                url: livekitUrl,
                token,
                transport: "livekit",
            }),
        });

        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            console.error("❌ Beyond Presence start failed:", data);
            return res.status(response.status).json({
                error: "Failed to start avatar session",
                details: data,
            });
        }

        return res.json({
            status: "success",
            avatar_identity: avatarIdentity,
            avatar_session: data,
        });
    } catch (error) {
        console.error("❌ startAvatar error:", error);
        return res.status(500).json({ error: "Avatar startup failed" });
    }
});

// Manual AI Summary Generation
app.post("/generateSummary", async (req, res) => {
    const { transcript } = req.body;
    if (!transcript || !Array.isArray(transcript)) {
        return res.status(400).json({ error: "No transcript provided" });
    }

    try {
        const transcriptText = transcript
            .map((t) => `${t.speaker || t.role}: ${t.text}`)
            .join("\n");

        const prompt = `Summarize this conversation based on the transcript below. 
Include:
1. Main topic discussed
2. Key questions asked by the user
3. Answers provided by the assistant
4. Any follow-up needed

Transcript:
${transcriptText}

Keep it professional and concise (max 150 words).`;

        const resp = await fetch("https://api.cerebras.ai/v1/chat/completions", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                Authorization: `Bearer ${process.env.CEREBRAS_API_KEY}`,
            },
            body: JSON.stringify({
                model: "llama-3.3-70b",
                messages: [
                    { role: "system", content: "You are a professional assistant summarizing a call." },
                    { role: "user", content: prompt },
                ],
            }),
        });

        const data = await resp.json();
        const summaryText = data.choices[0].message.content;
        res.json({ summary_text: summaryText });
    } catch (error) {
        console.error("❌ Summary generation error:", error);
        res.status(500).json({ error: "Failed to generate summary" });
    }
});

// Deepgram Voice Agent WebSocket proxy
const wss = new WebSocketServer({ server, path: "/agent-proxy" });
wss.on("connection", (browserWs) => {
    const dgWs = new WebSocket("wss://agent.deepgram.com/v1/agent/converse", {
        headers: { Authorization: `Token ${process.env.DEEPGRAM_API_KEY}` },
    });
    const queue = [];  // buffer browser messages until Deepgram connection is open
    dgWs.on("open", () => {
        console.log("🔗 Deepgram agent connected");
        queue.forEach(([data, isBinary]) => dgWs.send(data, { binary: isBinary }));
        queue.length = 0;
    });
    dgWs.on("message", (data, isBinary) => { if (browserWs.readyState === 1) browserWs.send(data, { binary: isBinary }); });
    dgWs.on("close",   ()    => browserWs.close());
    dgWs.on("error",   (e)   => console.error("DG WS error:", e.message));
    browserWs.on("message", (data, isBinary) => {
        if (dgWs.readyState === 1) dgWs.send(data, { binary: isBinary });
        else queue.push([data, isBinary]);
    });
    browserWs.on("close", () => dgWs.close());
});

const PORT = Number(process.env.PORT || 8080);
server.listen(PORT, () => console.log(`🚀 API/Proxy up → http://localhost:${PORT}`));
