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

# Create app directory structure
RUN mkdir -p /app/app

# Copy necessary files for this service
COPY app/anpr_web.py /app/app/
COPY app/models.py /app/app/
COPY app/templates/ /app/app/templates/
COPY app/static/ /app/app/static/

# Create __init__.py to make app a package
RUN touch /app/app/__init__.py

# Set PYTHONPATH so imports work
ENV PYTHONPATH=/app

# This service does not need the Dahua SDK or its system dependencies.
# Expose the web interface port
EXPOSE 5000
