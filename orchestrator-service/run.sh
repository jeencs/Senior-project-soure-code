#!/bin/bash

PORT=8001
VENV_DIR="venv"

echo "🚀 Starting orchestrator-service setup..."

if lsof -Pi :$PORT -sTCP:LISTEN -t >/dev/null ; then
    echo "⚠️  Port $PORT is already in use. Attempting to kill the existing process..."
    lsof -ti :$PORT | xargs kill -9
fi

if [ ! -d "$VENV_DIR" ]; then
    echo "📦 Creating virtual environment..."
    python3 -m venv $VENV_DIR
fi

source $VENV_DIR/bin/activate

echo "📥 Installing dependencies..."
pip install -r requirements.txt

export RABBITMQ_HOST=${RABBITMQ_HOST:-localhost}
export MONGO_URI=${MONGO_URI:-mongodb://localhost:27017}
export MINIO_ENDPOINT=${MINIO_ENDPOINT:-localhost:9000}
export MINIO_ACCESS_KEY=${MINIO_ACCESS_KEY:-minioadmin}
export MINIO_SECRET_KEY=${MINIO_SECRET_KEY:-minioadmin}
export DEEPSEEK_API_KEY=${DEEPSEEK_API_KEY:-your_key_here}

echo "🏃 Running service..."
uvicorn app.main:app --host 0.0.0.0 --port $PORT --reload
