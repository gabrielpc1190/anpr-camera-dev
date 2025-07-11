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

# Copy the application code into the container (excluding the .whl file now, will be copied separately)
COPY ./app/ /app/

# Install the Dahua SDK and move its libraries
# First, copy only the .whl file to a specific location to better leverage Docker cache.
# This assumes there's only one .whl file in the app directory.
COPY ./app/*.whl /tmp/sdk.whl
RUN pip install /tmp/sdk.whl && \
    mkdir -p /usr/local/lib/dahua_sdk && \
    cp /usr/local/lib/python3.11/site-packages/NetSDK/Libs/linux64/*.so /usr/local/lib/dahua_sdk/ && \
    rm -f /tmp/sdk.whl && \
    rm -f /app/*.whl # Clean up .whl from /app if it was copied by the broader COPY ./app/ /app/

# Expose the web interface port
EXPOSE 5000