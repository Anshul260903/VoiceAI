# Voice AI setup

## Environment variables

Create a `.env` file in the repo root for the server:

```
LIVEKIT_API_KEY=your_livekit_api_key
LIVEKIT_API_SECRET=your_livekit_api_secret
```

Optional (frontend): set the LiveKit URL used by the client:

```
VITE_LIVEKIT_URL=wss://your-livekit-cloud-domain
```

## Agent environment

Create a `.env` file inside `agent/` for the voice agent:

```
LIVEKIT_URL=wss://your-livekit-cloud-domain
LIVEKIT_API_KEY=your_livekit_api_key
LIVEKIT_API_SECRET=your_livekit_api_secret
DEEPGRAM_API_KEY=your_deepgram_api_key
PERPLEXITY_API_KEY=your_perplexity_api_key
CARTESIA_API_KEY=your_cartesia_api_key
```

## Run

From the repo root:

```
npm install
npm run dev
```

From `agent/` in a separate terminal:

```
source venv/bin/activate
python agent.py
```
