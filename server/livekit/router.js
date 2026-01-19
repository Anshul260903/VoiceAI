import express from "express";
import { AccessToken, RoomServiceClient } from "livekit-server-sdk";

export function createLivekitRouter() {
    const router = express.Router();

    const {
        LIVEKIT_URL,
        LIVEKIT_API_KEY,
        LIVEKIT_API_SECRET,
    } = process.env;

    if (!LIVEKIT_URL || !LIVEKIT_API_KEY || !LIVEKIT_API_SECRET) {
        throw new Error("LiveKit environment variables are missing");
    }

    const roomService = new RoomServiceClient(
        LIVEKIT_URL,
        LIVEKIT_API_KEY,
        LIVEKIT_API_SECRET
    );

    router.get("/getToken", async (req, res) => {
        const { roomName, identity } = req.query;

        if (!roomName || !identity) {
            return res.status(400).json({
                error: "roomName and identity are required",
            });
        }

        try {
            console.log(`📦 Creating room: ${roomName} with agent dispatch...`);

            // AWAIT room creation to ensure agent dispatch happens
            try {
                const room = await roomService.createRoom({
                    name: roomName,
                    agents: [{ agentName: "voice-agent" }],
                });
                console.log(`✅ Room created: ${room.name}, agent dispatched`);
            } catch (roomErr) {
                // Room might already exist, try to update it with agent
                console.log(`⚠️ Room may exist, dispatching agent...`);
                try {
                    await roomService.updateRoomMetadata(roomName, JSON.stringify({ agentRequested: true }));
                } catch (e) {
                    // Ignore - room might not exist yet
                }
            }

            // Small delay to ensure agent dispatch is processed
            await new Promise(resolve => setTimeout(resolve, 200));

            // Generate token
            const at = new AccessToken(
                LIVEKIT_API_KEY,
                LIVEKIT_API_SECRET,
                { identity, name: identity }
            );

            at.addGrant({
                roomJoin: true,
                room: roomName,
                canPublish: true,
                canSubscribe: true,
                canPublishData: true,
                agent: true,
            });

            const token = await at.toJwt();
            console.log(`🎫 Token generated for ${identity} in ${roomName}`);
            res.json({ token });
        } catch (err) {
            console.error("Token error:", err);
            res.status(500).json({ error: "Failed to create token" });
        }
    });

    return router;
}
