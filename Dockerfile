FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1
ENV HF_HOME=/models/huggingface

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# requirements.txt lists a bare "torch" with no build variant pinned, so a
# plain `pip install -r requirements.txt` pulls PyPI's DEFAULT wheel, which
# bundles full CUDA runtime libraries — several hundred MB to ~1GB of dead
# weight on a CPU-only Cloud Run instance with no GPU at all. Installing the
# CPU-only build FIRST from PyTorch's own CPU wheel index satisfies the
# "torch" requirement before pip ever considers the CUDA one, cutting both
# build time (this is the single biggest slice of a fresh build's time) and
# the final image size significantly. Safe no-op locally too: outside a
# CPU-only environment you'd still want to pin the real GPU build
# explicitly rather than rely on this Dockerfile's default.
RUN pip install --upgrade pip \
    && pip install torch --index-url https://download.pytorch.org/whl/cpu \
    && pip install -r requirements.txt

COPY app/ ./app/
COPY main.py .

CMD ["sh", "-c", "exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1"]
