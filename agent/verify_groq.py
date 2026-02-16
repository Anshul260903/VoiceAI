
import os
import asyncio
import json
from dotenv import load_dotenv
from openai import AsyncOpenAI
from datetime import datetime

# Load env
load_dotenv(".env")

# ------------------------------------------------------------------------------
# MOCK AGENT LOGIC (Replicating agent.py structure)
# ------------------------------------------------------------------------------

# 1. The Exact System Prompt from agent.py
BASE_SYSTEM_PROMPT = f"""You are a specialized Knowledge Base Voice Assistant.
    
CRITICAL INSTRUCTIONS:
1. You have NO internal knowledge. You can ONLY answer by searching the database via the `search_knowledge_base` tool.
2. For EVERY user query, you MUST call `search_knowledge_base`.
3. If the tool returns information, use it to answer the user's question.
4. If the tool returns "No relevant documents found" or if the answer is not in the tool output, you MUST say exactly:
   "I'm sorry, I don't have any information related to that in my documents."
5. Do NOT use your own training data. Do NOT hallucinate.

Today's date is {datetime.now().strftime("%Y-%m-%d")}.
"""

# 2. The Frontend "Default Prompt" (Simulating what App.jsx sends)
FRONTEND_PROMPT = """You are a specialized Knowledge Base Voice Assistant.
TIMELINE: Today's date is 2/16/2026.

CRITICAL RULES:
1. You have NO internal knowledge. You can ONLY answer by searching the database.
2. For EVERY user query, you MUST call the "search_knowledge_base" tool.
3. If the search tool returns information, use it to answer directly and concisely.
4. If the search tool returns "No relevant documents found", or if the user asks about something not in the documents, you MUST say exactly:
   "I'm sorry, I don't have any information related to that in my documents."
5. Do NOT make up facts. Do NOT use your own training data.
6. Keep responses short and conversational."""

# Combine them like agent.py does
FULL_SYSTEM_PROMPT = f"{BASE_SYSTEM_PROMPT}\n\n--- User Context / Persona ---\n{FRONTEND_PROMPT}"

async def run_test(name, user_query, mock_kb_result, expected_phrase=None, forbidden_phrase=None):
    print(f"\n🔍 TEST: {name}")
    print(f"   Query: {user_query}")
    print(f"   Mock KB: {mock_kb_result}")
    
    client = AsyncOpenAI(
        base_url="https://api.groq.com/openai/v1",
        api_key=os.environ.get("GROQ_API_KEY"),
    )
    
    # Simulate the conversation flow:
    # 1. User asks question
    # 2. Agent (simulated) calls tool (we skip the actual tool call step and inject the result)
    # 3. We provide the tool output to the LLM
    
    messages = [
        {"role": "system", "content": FULL_SYSTEM_PROMPT},
        {"role": "user", "content": user_query},
        # Simulate the tool execution result
        {"role": "tool", "tool_call_id": "call_mock_123", "content": mock_kb_result} 
        # Note: In real life, there's an assistant message with tool_calls before this, 
        # but for text-only simulation, we can just imply context or provide it as system context.
        # Actually, to be accurate to valid chat format, we usually need the assistant call.
        # Let's try a simpler approach: Just give the context in the system prompt for the test.
    ]
    
    # REVISED STRATEGY for valid simplified testing:
    # We will inject the "Search Result" directly into a system message to simulate "Tool Output having been processed"
    # This is how many RAG chains work simply.
    
    test_messages = [
        {"role": "system", "content": FULL_SYSTEM_PROMPT},
        {"role": "system", "content": f"TOOL OUTPUT for query '{user_query}':\n{mock_kb_result}"},
        {"role": "user", "content": user_query}
    ]

    try:
        completion = await client.chat.completions.create(
            model="llama-3.3-70b-versatile", # Updated to supported model
            messages=test_messages,
            temperature=0.6,
            max_tokens=150
        )
        response = completion.choices[0].message.content
        print(f"   🤖 Response: \"{response}\"")
        
        passed = True
        if expected_phrase and expected_phrase.lower() not in response.lower():
            print(f"   ❌ FAILED: Expected phrase '{expected_phrase}' missing.")
            passed = False
        
        if forbidden_phrase and forbidden_phrase.lower() in response.lower():
            print(f"   ❌ FAILED: Forbidden phrase '{forbidden_phrase}' present.")
            passed = False
            
        if passed:
            print("   ✅ PASSED")
            
    except Exception as e:
        print(f"   ❌ ERROR: {e}")

async def start_chat():
    print("========================================")
    print("    INTERACTIVE RAG VERIFICATION")
    print("========================================")
    print("Type 'exit' to quit.")
    print("You can simulate context (RAG results) for each query.")
    
    client = AsyncOpenAI(
        base_url="https://api.groq.com/openai/v1",
        api_key=os.environ.get("GROQ_API_KEY"),
    )

    while True:
        print("\n----------------------------------------")
        user_query = input("Enter your question: ").strip()
        if user_query.lower() in ("exit", "quit"):
            break
        
        if not user_query:
            continue

        use_context = input("Provide mock RAG context? (y/n): ").lower().strip() == 'y'
        mock_kb_result = "No relevant documents found."
        
        if use_context:
            print("Enter context (press Enter twice to finish):")
            lines = []
            while True:
                line = input()
                if not line:
                    break
                lines.append(line)
            mock_kb_result = "\n".join(lines)
            if not mock_kb_result:
                mock_kb_result = "No relevant documents found."

        print(f"\nProcessing with Context: {mock_kb_result[:50]}..." if len(mock_kb_result) > 50 else f"\nProcessing with Context: {mock_kb_result}")

        # Construct messages strictly like the agent
        messages = [
            {"role": "system", "content": FULL_SYSTEM_PROMPT},
            {"role": "system", "content": f"TOOL OUTPUT for query '{user_query}':\n{mock_kb_result}"},
            {"role": "user", "content": user_query}
        ]
        
        try:
            print("🤖 Agent thinking...")
            completion = await client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=messages,
                temperature=0.6,
                max_tokens=150
            )
            response = completion.choices[0].message.content
            print(f"\n➡️ RESPONSE:\n{response}")
            
        except Exception as e:
            print(f"❌ ERROR: {e}")

if __name__ == "__main__":
    asyncio.run(start_chat())
