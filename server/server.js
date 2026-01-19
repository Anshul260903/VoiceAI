// server/server.js
import dotenv from "dotenv";
dotenv.config();

import express from "express";
import { createServer } from "http";
import path from "path";
import { fileURLToPath } from "url";
import { createLivekitRouter } from "./livekit/router.js";

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

app.use(createLivekitRouter());

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

        const prompt = `Summarize this appointment booking conversation. 
Include:
1. Main purpose of the call
2. Actions taken (bookings, cancellations)
3. User preferences mentioned
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

const PORT = Number(process.env.PORT || 8080);
server.listen(PORT, () => console.log(`🚀 API/Proxy up → http://localhost:${PORT}`));
