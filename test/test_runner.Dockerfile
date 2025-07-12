# Dockerfile for the autonomous test runner
FROM python:3.11-slim-bookworm

# Set the working directory
WORKDIR /app

# Install the only dependency needed for testing
RUN pip install requests

# Copy the test script into the container
COPY test_runner.py .

# The command that will be executed when the container starts
CMD ["python", "test_runner.py"]