# Dockerfile for anpr_db_manager service
FROM python:3.11-slim-bookworm

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# Set work directory
WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY app/anpr_db_manager.py .
COPY app/models.py .

# Expose port
EXPOSE 5001


# Run the application
CMD ["gunicorn", "--bind", "0.0.0.0:5001", "--workers", "2", "--log-level", "warning", "anpr_db_manager:app"]
