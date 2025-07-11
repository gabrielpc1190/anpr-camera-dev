# Use an official Python runtime as a parent image
FROM python:3.11-slim-bookworm

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1
ENV LD_LIBRARY_PATH=/usr/local/lib/dahua_sdk
ENV PATH="/root/.local/bin:${PATH}"

# Install system dependencies required by the Dahua SDK and other tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    libasound2 \
    libxv1 \
    libgl1-mesa-glx \
    && rm -rf /var/lib/apt/lists/*

# Set the working directory
WORKDIR /app

# Copy requirements first to leverage Docker cache
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code into the container
# This assumes the .whl file is already in the 'app' directory on the host
COPY ./app/ /app/ # Explicitly copy to /app/ to be clear, WORKDIR is /app

# Install the Dahua SDK and move its libraries
# The .whl file (with its original name) is expected to be in /app (the WORKDIR)
# due to the 'COPY ./app/ /app/' command above.

RUN echo "--- Debugging and Installing SDK wheel file from /app ---" && \
    echo "Current directory (should be /app): $(pwd)" && \
    echo "Listing /app directory contents:" && ls -la /app && \
    WHL_FILE=$(find /app -maxdepth 1 -name '*.whl' -print -quit) && \
    if [ -z "$WHL_FILE" ]; then echo "ERROR: No .whl file found in /app directory!" >&2; exit 1; fi && \
    echo "Found wheel file: $WHL_FILE" && \
    echo "File details for $WHL_FILE:" && file "$WHL_FILE" && \
    echo "SHA256 checksum for $WHL_FILE:" && sha256sum "$WHL_FILE" && \
    echo "Attempting to install $WHL_FILE ..." && \
    pip install "$WHL_FILE" && \
    echo "Creating SDK library directory: /usr/local/lib/dahua_sdk" && \
    mkdir -p /usr/local/lib/dahua_sdk && \
    echo "Copying .so files..." && \
    cp /usr/local/lib/python3.11/site-packages/NetSDK/Libs/linux64/*.so /usr/local/lib/dahua_sdk/ && \
    echo "Cleaning up SDK wheel file: $WHL_FILE" && rm -f "$WHL_FILE" && \
    echo "--- SDK Installation Complete ---"

# Expose the web interface port
EXPOSE 5000