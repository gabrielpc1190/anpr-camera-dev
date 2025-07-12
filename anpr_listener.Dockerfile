# Dockerfile for anpr_listener service
FROM python:3.11-slim-bookworm

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1
ENV LD_LIBRARY_PATH=/usr/local/lib/dahua_sdk
ENV PATH="/root/.local/bin:${PATH}"

# Install system dependencies required by the Dahua SDK
RUN apt-get update && apt-get install -y --no-install-recommends \
    libasound2 \
    libxv1 \
    libgl1-mesa-glx \
    file \
    && rm -rf /var/lib/apt/lists/*

# Set the working directory
WORKDIR /app

# Copy requirements first to leverage Docker cache
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the listener script and the SDK wheel file
# The setup.sh script ensures the .whl file is in the app/ directory before building
COPY app/anpr_listener.py .
COPY app/*.whl .

# Install the Dahua SDK and move its libraries
RUN echo "--- Installing SDK wheel file ---" && \
    WHL_FILE=$(find . -maxdepth 1 -name '*.whl' -print -quit) && \
    if [ -z "$WHL_FILE" ]; then echo "ERROR: No .whl file found!" >&2; exit 1; fi && \
    pip install "$WHL_FILE" && \
    mkdir -p /usr/local/lib/dahua_sdk && \
    cp /usr/local/lib/python3.11/site-packages/NetSDK/Libs/linux64/*.so /usr/local/lib/dahua_sdk/ && \
    rm -f "$WHL_FILE" && \
    echo "--- SDK Installation Complete ---"

