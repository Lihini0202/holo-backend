# Use Python 3.10
FROM python:3.10-slim

# Set working directory
WORKDIR /app

# Install system dependencies needed by grpcio/google-cloud
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy all your code into the container
COPY . .

# Fix Windows line endings and set permissions
RUN sed -i 's/\r$//' start.sh && chmod +x start.sh

# Create a non-root user (Required by Choreo security)
RUN useradd -m choreo
USER choreo

# Start the app
CMD ["./start.sh"]