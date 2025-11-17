# Dockerfile for anpr_web service
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
COPY app/anpr_web.py .
COPY app/models.py .
COPY app/templates /app/templates/
COPY app/static /app/static/

# Expose port
EXPOSE 5000

# Create a non-root user
RUN useradd -ms /bin/bash appuser
USER appuser

# Run the application
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "4", "anpr_web:app"]
