import asyncio
import json
import os
import logging
import tempfile
from pathlib import Path
from datetime import datetime
from fastapi import FastAPI
from motor.motor_asyncio import AsyncIOMotorClient
from minio import Minio

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="PDF Renderer Service")

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

async def download_assets(bucket: str, tenant_id: str, job_id: str, md_key: str, md_path: Path, img_dir: Path):
    logger.info(f"Fetching translated Markdown for job {job_id}")
    minio_client.fget_object(bucket, md_key, str(md_path))

    images = minio_client.list_objects(bucket, prefix=f"{tenant_id}/{job_id}/images/", recursive=True)
    for img in images:
        img_filename = Path(img.object_name).name
        minio_client.fget_object(bucket, img.object_name, str(img_dir / img_filename))

def create_latex_header(header_path: Path):
    with open(header_path, "w") as f:
        f.write(r"""
\usepackage[margin=1.2in, a4paper]{geometry}
\usepackage{fontspec}
\usepackage{amsmath}
\usepackage{amssymb}
\usepackage{amsfonts}
\usepackage{unicode-math}
\usepackage{graphicx}
\usepackage{titlesec}
\usepackage{xcolor}
\usepackage{microtype}
\usepackage{setspace}
\usepackage{parskip}
\usepackage{caption}

\setstretch{1.3}
\microtypesetup{protrusion=true, tracking=true}

\definecolor{primary}{HTML}{111827}
\definecolor{secondary}{HTML}{4B5563}

\titleformat{\section}
  {\fontsize{24}{28}\bfseries\sffamily\color{primary}}
  {\thesection}{1em}{}
\titleformat{\subsection}
  {\fontsize{18}{22}\bfseries\sffamily\color{primary}}
  {\thesubsection}{1em}{}
\titleformat{\subsubsection}
  {\fontsize{14}{18}\bfseries\sffamily\color{secondary}}
  {\thesubsubsection}{1em}{}

\titlespacing*{\section}{0pt}{3.5ex plus 1ex minus .2ex}{2.3ex plus .2ex}
\titlespacing*{\subsection}{0pt}{3.25ex plus 1ex minus .2ex}{1.5ex plus .2ex}

\captionsetup{font=small, labelfont=bf, justification=centering}

\setlength{\parskip}{1.2ex plus 0.5ex minus 0.2ex}
\setlength{\parindent}{0pt}

\setmainfont{TeX Gyre Termes}
\setsansfont{TeX Gyre Heros}
""")

async def run_pandoc(md_path: Path, pdf_path: Path, header_path: Path, work_dir: Path):
    cmd = [
        "pandoc", str(md_path),
        "-o", str(pdf_path),
        "--pdf-engine=xelatex",
        "--from=markdown+smart",
        "-V", "fontsize=12pt",
        f"--resource-path={work_dir}",
        f"--include-in-header={header_path}"
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(work_dir)
    )
    _, stderr = await proc.communicate()

    if proc.returncode != 0:
        error_msg = stderr.decode()
        logger.error(f"Pandoc failed: {error_msg}")
        raise Exception(f"Pandoc error: {error_msg}")

async def render_pdf(job_id: str, tenant_id: str, bucket: str, md_key: str):
    with tempfile.TemporaryDirectory() as tmp_dir:
        work_dir = Path(tmp_dir)
        md_path = work_dir / "final.md"
        pdf_path = work_dir / "translated.pdf"
        img_dir = work_dir / "images"
        header_path = work_dir / "header.tex"

        img_dir.mkdir(exist_ok=True)

        await download_assets(bucket, tenant_id, job_id, md_key, md_path, img_dir)
        create_latex_header(header_path)

        logger.info(f"Running Pandoc for job {job_id}")
        await run_pandoc(md_path, pdf_path, header_path, work_dir)

        pdf_key = f"{tenant_id}/{job_id}/output/translated.pdf"
        minio_client.fput_object(bucket, pdf_key, str(pdf_path), content_type="application/pdf")

        return pdf_key, pdf_path.stat().st_size



@app.on_event("startup")
async def startup_event():
    pass

@app.post("/render")
async def render_sync(request: dict):
    job_id = request.get("jobId")
    if not job_id:
        return {"error": "jobId is required"}, 400

    logger.info(f"Synchronous rendering request for jobId: {job_id}")

    try:
        job = await db.translation_jobs.find_one({"_id": job_id})
        if not job:
            return {"error": "Job not found"}, 404

        if job.get("status") not in ["TRANSLATED", "RENDERING"]:
             logger.warning(f"Job {job_id} is in status {job.get('status')}, might not be ready for rendering")

        tenant_id = job.get("tenantId")
        md_key = job.get("translated", {}).get("markdownKey")
        bucket = job.get("input", {}).get("bucket", "documents")

        if not md_key:
            return {"error": "Markdown key missing"}, 400

        await db.translation_jobs.update_one(
            {"_id": job_id},
            {"$set": {"status": "RENDERING", "updatedAt": datetime.utcnow()}}
        )

        pdf_key, pdf_size = await render_pdf(job_id, tenant_id, bucket, md_key)

        await db.translation_jobs.update_one(
            {"_id": job_id},
            {
                "$set": {
                    "status": "COMPLETED",
                    "updatedAt": datetime.utcnow(),
                    "output.pdfKey": pdf_key,
                    "output.sizeBytes": pdf_size
                }
            }
        )
        return {"status": "success", "jobId": job_id, "pdfKey": pdf_key}

    except Exception as e:
        logger.exception(f"Error in sync rendering for job {job_id}: {e}")
        await db.translation_jobs.update_one(
            {"_id": job_id},
            {"$set": {"status": "FAILED", "updatedAt": datetime.utcnow(),
                       "failureReason": str(e)}}
        )
        return {"status": "error", "message": str(e)}, 500

@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "renderer-service"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8002)
