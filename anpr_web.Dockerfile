# Dockerfile for anpr_web service
FROM python:3.11-slim-bookworm

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1
ENV PATH="/root/.local/bin:${PATH}"

# Set the working directory
WORKDIR /app

# Copy requirements first to leverage Docker cache
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install curl for health checks
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

# Copy only the necessary files for this service
COPY app/anpr_web.py .
COPY app/templates/ /app/templates/

# This service does not need the Dahua SDK or its system dependencies.
# Expose the web interface port
EXPOSE 5000
