import asyncio
import json
import os
import logging
import httpx
from datetime import datetime
from io import BytesIO
from typing import List
from fastapi import FastAPI
from motor.motor_asyncio import AsyncIOMotorClient
from minio import Minio

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Translation Orchestrator Service")

RABBITMQ_HOST = os.getenv("RABBITMQ_HOST", "localhost")
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "localhost:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "minioadmin")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "your_key_here")
DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"

DEEPSEEK_HTTP_TIMEOUT = float(os.getenv("DEEPSEEK_HTTP_TIMEOUT_SECONDS", "600"))

mongo_client = AsyncIOMotorClient(MONGO_URI)
db = mongo_client.translation_db
minio_client = Minio(
    MINIO_ENDPOINT.replace("http://", ""),
    access_key=MINIO_ACCESS_KEY,
    secret_key=MINIO_SECRET_KEY,
    secure=False
)

async def chunk_markdown(content: str, max_tokens: int = 4000) -> List[str]:
    paragraphs = content.split("\n\n")
    chunks = []
    current_chunk = ""

    for p in paragraphs:
        if len(current_chunk) + len(p) < max_tokens * 4:
            current_chunk += p + "\n\n"
        else:
            chunks.append(current_chunk.strip())
            current_chunk = p + "\n\n"

    if current_chunk:
        chunks.append(current_chunk.strip())

    return chunks

async def track_translation_progress(job_id: str, prompt_tokens: int, completion_tokens: int):
    cost = (prompt_tokens / 1_000_000) * 0.14 + (completion_tokens / 1_000_000) * 0.28

    await db.translation_jobs.update_one(
        {"_id": job_id},
        {
            "$inc": {
                "costs.currentSpent": cost,
                "translated.completedChunks": 1
            }
        }
    )

async def translate_chunk(chunk: str, source_lang: str, target_lang: str, job_id: str, options: dict = None) -> str:
    options = options or {}
    style = options.get("style", "natural")
    tone = options.get("tone", "neutral")
    doc_type = options.get("docType", "general")
    formatting = options.get("formatting", "preserve")
    keep_terms = options.get("keepTerms", False)
    custom_prompt = options.get("customPrompt", "")

    prompt = f"""You are a professional translator specializing in {doc_type} documents.
Translate the following Markdown content from {source_lang} to {target_lang}.

Translation Requirements:
- Style: {style}
- Tone: {tone}
- Formatting: {formatting}
- Technical Terms: {"Keep technical terms/proper nouns untranslated where appropriate" if keep_terms else "Translate technical terms naturally"}
- Specific Instructions: {custom_prompt if custom_prompt else "None"}

Rules:
- Preserve all Markdown syntax exactly: headings, bold, italics, tables, code blocks, links.
- Code Sections: If the content contains code blocks or inline code, these must be handled with care. The code itself should remain functional and intact, but you should translate any comments or strings within the code if they are in the source language. Ensure the output is returned in correctly formatted Markdown code blocks.
- Formulas: Ensure all mathematical formulas (LaTeX) are preserved and formatted perfectly.
- Leave all LaTeX formulas ($...$ or $$...$$) exactly as they are.
- NEVER use LaTeX commands (like \mathcal, \mathbb, etc.) outside of math delimiters ($...$ or $$...$$).
- If you see a LaTeX command in the source that is not in math mode, wrap it in $ ... $ in the translation.
- Do NOT translate image alt text or file paths. (put a image label there so the user could see it was image)
- Output ONLY the translated Markdown, no preamble.

Content:
{chunk}
"""

    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": "You are a professional translator."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.7,
        "stream": False
    }

    timeout = httpx.Timeout(DEEPSEEK_HTTP_TIMEOUT, connect=60.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(DEEPSEEK_API_URL, headers=headers, json=payload)
        response.raise_for_status()
        result = response.json()

        usage = result.get("usage", {})
        await track_translation_progress(
            job_id,
            usage.get("prompt_tokens", 0),
            usage.get("completion_tokens", 0)
        )

        return result["choices"][0]["message"]["content"]



@app.on_event("startup")
async def startup_event():
    pass

@app.post("/translate")
async def translate_sync(request: dict):
    job_id = request.get("jobId")
    if not job_id:
        return {"error": "jobId is required"}, 400

    logger.info(f"Synchronous translation request for jobId: {job_id}")

    try:
        job = await db.translation_jobs.find_one({"_id": job_id})
        if not job:
            return {"error": "Job not found"}, 404


        if job.get("status") not in ["CONVERTED", "TRANSLATING"]:
            logger.warning(f"Job {job_id} is in status {job.get('status')}, might not be ready for translation")

        tenant_id = job.get("tenantId")
        source_lang = job.get("sourceLanguage", "en")
        target_lang = job.get("targetLanguage", "de")
        md_key = job.get("converted", {}).get("markdownKey")
        bucket = job.get("input", {}).get("bucket", "documents")

        if not md_key:
            return {"error": "Markdown key missing"}, 400

        await db.translation_jobs.update_one(
            {"_id": job_id},
            {"$set": {"status": "TRANSLATING", "updatedAt": datetime.utcnow()}}
        )

        response = minio_client.get_object(bucket, md_key)
        content = response.read().decode("utf-8")
        response.close()

        chunks = await chunk_markdown(content)
        await db.translation_jobs.update_one(
            {"_id": job_id},
            {"$set": {"translated.chunkCount": len(chunks), "translated.completedChunks": 0}}
        )

        options = job.get("options", {})

        translated_chunks = []
        for chunk in chunks:
            translated_chunk = await translate_chunk(chunk, source_lang, target_lang, job_id, options)
            translated_chunks.append(translated_chunk)

        final_md = "\n\n".join(translated_chunks)
        final_md_key = f"{tenant_id}/{job_id}/translated/final.md"
        md_bytes = BytesIO(final_md.encode("utf-8"))
        minio_client.put_object(
            bucket,
            final_md_key,
            md_bytes,
            length=md_bytes.getbuffer().nbytes,
            content_type="text/markdown"
        )

        await db.translation_jobs.update_one(
            {"_id": job_id},
            {
                "$set": {
                    "status": "TRANSLATED",
                    "updatedAt": datetime.utcnow(),
                    "translated.markdownKey": final_md_key
                }
            }
        )
        return {"status": "success", "jobId": job_id, "markdownKey": final_md_key}

    except Exception as e:
        logger.exception(f"Error in sync translation for job {job_id}: {e}")
        await db.translation_jobs.update_one(
            {"_id": job_id},
            {"$set": {"status": "FAILED", "updatedAt": datetime.utcnow(),
                       "failureReason": str(e)}}
        )
        return {"status": "error", "message": str(e)}, 500

@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "orchestrator-service"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
