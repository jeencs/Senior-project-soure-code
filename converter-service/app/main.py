import asyncio
import json
import os
import logging
import tempfile
from datetime import datetime
from io import BytesIO
from fastapi import FastAPI
from motor.motor_asyncio import AsyncIOMotorClient
from minio import Minio
from docling.document_converter import DocumentConverter
from docling_core.types.doc import PictureItem

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="PDF-to-Markdown Converter Service")

RABBITMQ_HOST = os.getenv("RABBITMQ_HOST", "localhost")
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "localhost:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "minioadmin")

mongo_client = AsyncIOMotorClient(MONGO_URI)
db = mongo_client.translation_db
minio_client = Minio(
    MINIO_ENDPOINT.replace("http://", ""),
    access_key=MINIO_ACCESS_KEY,
    secret_key=MINIO_SECRET_KEY,
    secure=False
)

from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.datamodel.base_models import InputFormat
from docling.document_converter import DocumentConverter, PdfFormatOption

pipeline_options = PdfPipelineOptions()
pipeline_options.generate_picture_images = True
pipeline_options.images_scale = 2.0

doc_converter = DocumentConverter(
    format_options={
        InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
    }
)

async def upload_images(doc, tenant_id, job_id, source_bucket):
    image_count = 0
    for item, _level in doc.iterate_items():
        if not isinstance(item, PictureItem):
            continue

        image_count += 1
        image = item.get_image(doc=doc)
        if image is None:
            logger.warning(f"PictureItem {image_count} returned no image data, skipping")
            continue

        image_bytes = BytesIO()
        image.save(image_bytes, format="PNG")
        image_bytes.seek(0)

        image_key = f"{tenant_id}/{job_id}/images/img{image_count:03d}.png"
        minio_client.put_object(
            source_bucket,
            image_key,
            image_bytes,
            length=image_bytes.getbuffer().nbytes,
            content_type="image/png"
        )
        logger.info(f"Uploaded image {image_count} → {image_key}")

    return image_count

def rewrite_image_references(md_content: str, image_count: int) -> str:
    import re
    counter = [0]

    def replace_match(m):
        counter[0] += 1
        idx = counter[0]
        if idx > image_count:
            return m.group(0)
        alt_text = m.group(1) or f"Figure {idx}"
        return f"![{alt_text}](./images/img{idx:03d}.png)"

    md_content = re.sub(r'!\[([^\]]*)\]\([^)]*\)', replace_match, md_content)

    def replace_stub(m):
        counter[0] += 1
        idx = counter[0]
        if idx > image_count:
            return m.group(0)
        return f"![Figure {idx}](./images/img{idx:03d}.png)"

    md_content = re.sub(r'<!--\s*image\s*-->', replace_stub, md_content)
    return md_content


async def upload_conversion_results(job_id, tenant_id, source_bucket, md_content, image_count):
    md_content = rewrite_image_references(md_content, image_count)

    md_key = f"{tenant_id}/{job_id}/converted/structured.md"
    md_bytes = BytesIO(md_content.encode("utf-8"))
    minio_client.put_object(
        source_bucket,
        md_key,
        md_bytes,
        length=md_bytes.getbuffer().nbytes,
        content_type="text/markdown"
    )

    manifest = {
        "jobId": job_id,
        "imageCount": image_count,
        "convertedAt": datetime.utcnow().isoformat(),
        "version": "1.0"
    }
    manifest_key = f"{tenant_id}/{job_id}/converted/manifest.json"
    manifest_bytes = BytesIO(json.dumps(manifest).encode("utf-8"))
    minio_client.put_object(
        source_bucket,
        manifest_key,
        manifest_bytes,
        length=manifest_bytes.getbuffer().nbytes,
        content_type="application/json"
    )
    return md_key, manifest_key



@app.on_event("startup")
async def startup_event():
    pass

@app.post("/convert")
async def convert_sync(request: dict):
    job_id = request.get("jobId")
    if not job_id:
        return {"error": "jobId is required"}, 400

    logger.info(f"Synchronous conversion request for jobId: {job_id}")


    try:
        job = await db.translation_jobs.find_one({"_id": job_id})
        if not job:
            return {"error": "Job not found"}, 404

        tenant_id = job.get("tenantId")
        source_info = job.get("input", {})
        source_bucket = source_info.get("bucket", "documents")
        source_key = source_info.get("key")

        if not source_key:
            return {"error": "Source key missing"}, 400

        await db.translation_jobs.update_one(
            {"_id": job_id},
            {"$set": {"status": "CONVERTING", "updatedAt": datetime.utcnow()}}
        )

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp_file:
            tmp_path = tmp_file.name
            minio_client.fget_object(source_bucket, source_key, tmp_path)

        try:
            result = doc_converter.convert(tmp_path)
            doc = result.document
            md_content = doc.export_to_markdown()

            image_count = await upload_images(doc, tenant_id, job_id, source_bucket)
            md_key, manifest_key = await upload_conversion_results(
                job_id, tenant_id, source_bucket, md_content, image_count
            )

            await db.translation_jobs.update_one(
                {"_id": job_id},
                {
                    "$set": {
                        "status": "CONVERTED",
                        "updatedAt": datetime.utcnow(),
                        "converted": {
                            "markdownKey": md_key,
                            "manifestKey": manifest_key,
                            "confidenceScore": 1.0
                        }
                    }
                }
            )
            return {"status": "success", "jobId": job_id, "markdownKey": md_key}

        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    except Exception as e:
        logger.exception(f"Error in sync conversion for job {job_id}: {e}")
        await db.translation_jobs.update_one(
            {"_id": job_id},
            {"$set": {"status": "FAILED", "updatedAt": datetime.utcnow(),
                       "failureReason": str(e)}}
        )
        return {"status": "error", "message": str(e)}, 500

@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "converter-service"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
