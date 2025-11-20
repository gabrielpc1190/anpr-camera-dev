# Dockerfile for anpr_db_manager service
FROM python:3.11-slim-bookworm

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# Set the working directory
WORKDIR /app

# Copy requirements first to leverage Docker cache
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install curl for health checks
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

# Copy only the necessary files for this service
COPY app/anpr_db_manager.py .

EXPOSE 5001

# The default command to run when the container starts
CMD ["python", "anpr_db_manager.py"]