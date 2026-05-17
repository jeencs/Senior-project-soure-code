# Book Translation Experiment
Upload a PDF book, translate it to another language with AI, and download the translated PDF.
## What it does
1. **Upload** – You send a PDF through the web page.
2. **Convert** – The PDF is turned into Markdown (text + images).
3. **Translate** – The text is translated using the DeepSeek API.
4. **Render** – The translated text is turned back into a PDF.
5. **Download** – When the job is done, you download the new PDF.

## What you need
- [Docker](https://www.docker.com/) and Docker Compose
- [Node.js](https://nodejs.org/) (includes **npm**) 20.x for Web app and API (`app/`)
- [Python](https://www.python.org/) version 3.11+ for Converter, orchestrator, and renderer services
- A [DeepSeek](https://platform.deepseek.com/) API key (for translation)
  
## Quick start
### 1. Clone the repo
git clone https://github.com/YOUR_USERNAME/book-translation-experiement.git
cd book-translation-experiement

### 2. Set your API key
Create a .env file in the project root (same folder as docker-compose.yml):

DEEPSEEK_API_KEY=your_api_key_here

### 3. Start MongoDB replica set (first time only)
MongoDB must be running as a replica set. After the first docker compose up, run. Wait a few seconds, then start (or restart) all services:
docker compose up --build

### 4. Open the app
In your browser go to:
**http://localhost:8080**
Upload a PDF, select the style and target languages, and start a job.
Use **My Jobs** to check progress and download the result when status is **COMPLETED.**

## Services (ports)

**Service**    **Port**	   **Purpose**
Web app	      8080	 Upload PDFs and manage jobs
Converter 	  8000	 PDF → Markdown
Orchestrator  8001	 AI translation
Renderer	    8002	 Markdown → PDF
MinIO (files)	9000	 File storage
MinIO console	9001	 Storage admin UI
Mongo Express	8081	 Database admin UI (optional)
MongoDB	      27017	 Job and metadata storage

Default MinIO login: minioadmin / minioadmin

## Project structure
book-translation-experiement/
- app/                    # Web UI + API (Node.js)
- converter-service/      # PDF to Markdown (Python)
- orchestrator-service/   # Translation with DeepSeek (Python)
- renderer-service/       # Markdown to PDF (Python)
- docker-compose.yml      # Run everything together

## Troubleshooting
Jobs fail or MongoDB errors – Make sure you ran rs.initiate (step 3) and restarted Compose.
Translation fails – Check DEEPSEEK_API_KEY in .env and that the key is valid.
Download not available – Wait until job status is COMPLETED.
