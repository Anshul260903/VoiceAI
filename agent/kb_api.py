"""
Knowledge Base API Server
FastAPI server for document upload, chunking, embedding, and retrieval via ChromaDB.
Runs on port 8001 alongside the LiveKit agent.
"""

import os
import uuid
import logging
import json
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import chromadb
import uvicorn

# -------------------------
# Logging
# -------------------------
logger = logging.getLogger("kb-api")
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
logger.addHandler(handler)

# -------------------------
# ChromaDB Setup
# -------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
KB_DATA_DIR = os.path.join(BASE_DIR, "kb_data")
os.makedirs(KB_DATA_DIR, exist_ok=True)

# Use persistent ChromaDB with built-in sentence-transformer embeddings
chroma_client = chromadb.PersistentClient(path=KB_DATA_DIR)

# Get or create the main collection — uses default all-MiniLM-L6-v2 embedding
collection = chroma_client.get_or_create_collection(
    name="knowledge_base",
    metadata={"hnsw:space": "cosine"}
)

# In-memory document metadata store (persisted as JSON)
DOC_META_FILE = os.path.join(KB_DATA_DIR, "doc_metadata.json")

def load_doc_metadata():
    if os.path.exists(DOC_META_FILE):
        with open(DOC_META_FILE, "r") as f:
            return json.load(f)
    return {}

def save_doc_metadata(metadata):
    with open(DOC_META_FILE, "w") as f:
        json.dump(metadata, f, indent=2, default=str)

doc_metadata = load_doc_metadata()

# -------------------------
# Text Extraction
# -------------------------
def extract_text_from_file(file_path: str, filename: str) -> str:
    """Extract text content from uploaded file."""
    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    
    if ext == "txt":
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    
    elif ext == "pdf":
        try:
            import pdfplumber
            text_parts = []
            with pdfplumber.open(file_path) as pdf:
                for page in pdf.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text_parts.append(page_text)
            return "\n\n".join(text_parts)
        except ImportError:
            raise HTTPException(500, "pdfplumber not installed. Run: pip install pdfplumber")
    
    elif ext == "docx":
        try:
            from docx import Document
            doc = Document(file_path)
            return "\n\n".join([p.text for p in doc.paragraphs if p.text.strip()])
        except ImportError:
            raise HTTPException(500, "python-docx not installed. Run: pip install python-docx")
    
    elif ext in ("md", "markdown"):
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    
    else:
        # Try reading as plain text
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()

# -------------------------
# Chunking
# -------------------------
def chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> list[str]:
    """Split text into overlapping chunks."""
    if not text.strip():
        return []
    
    # Clean up whitespace
    text = " ".join(text.split())
    
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end]
        
        # Try to break at sentence boundary
        if end < len(text):
            last_period = chunk.rfind(".")
            last_newline = chunk.rfind("\n")
            break_point = max(last_period, last_newline)
            if break_point > chunk_size * 0.3:  # Only if we're past 30% of chunk
                chunk = chunk[:break_point + 1]
                end = start + break_point + 1
        
        if chunk.strip():
            chunks.append(chunk.strip())
        
        start = end - overlap if end < len(text) else len(text)
    
    return chunks

# -------------------------
# FastAPI App
# -------------------------
app = FastAPI(title="Voice AI Knowledge Base API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -------------------------
# Routes
# -------------------------

@app.get("/api/kb/health")
async def health():
    return {"status": "ok", "documents": len(doc_metadata), "chunks": collection.count()}


@app.post("/api/kb/upload")
async def upload_document(file: UploadFile = File(...)):
    """Upload a document, extract text, chunk, embed, and store in ChromaDB."""
    
    if not file.filename:
        raise HTTPException(400, "No filename provided")
    
    # Validate file type
    allowed_extensions = {"pdf", "txt", "docx", "md", "markdown"}
    ext = file.filename.lower().rsplit(".", 1)[-1] if "." in file.filename else ""
    if ext not in allowed_extensions:
        raise HTTPException(400, f"Unsupported file type: .{ext}. Allowed: {', '.join(allowed_extensions)}")
    
    # Save file temporarily
    doc_id = str(uuid.uuid4())[:8]
    upload_dir = os.path.join(KB_DATA_DIR, "uploads")
    os.makedirs(upload_dir, exist_ok=True)
    file_path = os.path.join(upload_dir, f"{doc_id}_{file.filename}")
    
    try:
        content = await file.read()
        with open(file_path, "wb") as f:
            f.write(content)
        
        # Extract text
        text = extract_text_from_file(file_path, file.filename)
        if not text.strip():
            raise HTTPException(400, "Could not extract any text from the file")
        
        # Chunk the text
        chunks = chunk_text(text)
        if not chunks:
            raise HTTPException(400, "No valid chunks could be created from the file")
        
        # Store chunks in ChromaDB
        chunk_ids = [f"{doc_id}_chunk_{i}" for i in range(len(chunks))]
        chunk_metadatas = [
            {
                "doc_id": doc_id,
                "doc_name": file.filename,
                "chunk_index": i,
                "total_chunks": len(chunks),
            }
            for i in range(len(chunks))
        ]
        
        collection.add(
            ids=chunk_ids,
            documents=chunks,
            metadatas=chunk_metadatas,
        )
        
        # Save document metadata
        doc_metadata[doc_id] = {
            "id": doc_id,
            "filename": file.filename,
            "file_type": ext,
            "text_length": len(text),
            "num_chunks": len(chunks),
            "uploaded_at": datetime.now().isoformat(),
            "file_path": file_path,
        }
        save_doc_metadata(doc_metadata)
        
        logger.info(f"✅ Document uploaded: {file.filename} → {len(chunks)} chunks, doc_id={doc_id}")
        
        return {
            "status": "success",
            "document": doc_metadata[doc_id],
            "message": f"Uploaded '{file.filename}' — {len(chunks)} chunks indexed",
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Upload failed: {e}")
        # Clean up on failure
        if os.path.exists(file_path):
            os.remove(file_path)
        raise HTTPException(500, f"Upload failed: {str(e)}")


@app.get("/api/kb/documents")
async def list_documents():
    """List all uploaded documents."""
    return {
        "documents": list(doc_metadata.values()),
        "total": len(doc_metadata),
        "total_chunks": collection.count(),
    }


@app.delete("/api/kb/documents/{doc_id}")
async def delete_document(doc_id: str):
    """Delete a document and all its chunks from ChromaDB."""
    
    if doc_id not in doc_metadata:
        raise HTTPException(404, f"Document {doc_id} not found")
    
    doc_info = doc_metadata[doc_id]
    
    try:
        # Delete chunks from ChromaDB
        chunk_ids = [f"{doc_id}_chunk_{i}" for i in range(doc_info["num_chunks"])]
        collection.delete(ids=chunk_ids)
        
        # Delete file
        if os.path.exists(doc_info.get("file_path", "")):
            os.remove(doc_info["file_path"])
        
        # Remove metadata
        del doc_metadata[doc_id]
        save_doc_metadata(doc_metadata)
        
        logger.info(f"🗑️ Document deleted: {doc_info['filename']} (doc_id={doc_id})")
        
        return {"status": "success", "message": f"Deleted '{doc_info['filename']}'"}
    
    except Exception as e:
        logger.error(f"❌ Delete failed: {e}")
        raise HTTPException(500, f"Delete failed: {str(e)}")


class QueryRequest(BaseModel):
    query: str
    top_k: int = 5

@app.post("/api/kb/query")
async def query_knowledge_base(req: QueryRequest):
    """Query the knowledge base and return relevant chunks."""
    
    if collection.count() == 0:
        return {"results": [], "message": "Knowledge base is empty"}
    
    try:
        results = collection.query(
            query_texts=[req.query],
            n_results=min(req.top_k, collection.count()),
        )
        
        formatted = []
        for i in range(len(results["ids"][0])):
            formatted.append({
                "chunk_id": results["ids"][0][i],
                "text": results["documents"][0][i],
                "metadata": results["metadatas"][0][i],
                "distance": results["distances"][0][i] if results.get("distances") else None,
            })
        
        logger.info(f"🔍 Query: '{req.query[:50]}...' → {len(formatted)} results")
        
        return {"results": formatted, "query": req.query}
    
    except Exception as e:
        logger.error(f"❌ Query failed: {e}")
        raise HTTPException(500, f"Query failed: {str(e)}")


# -------------------------
# Run
# -------------------------
if __name__ == "__main__":
    logger.info("🚀 Starting Knowledge Base API on port 8001...")
    uvicorn.run(app, host="0.0.0.0", port=8001)
