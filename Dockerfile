# Use Python 3.10
FROM python:3.10-slim

# Set working directory
WORKDIR /app

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy all your code into the container
COPY . .

# This removes invisible Windows characters so Linux can read the script.
RUN sed -i 's/\r$//' start.sh

# 2. Give permission to run the script
RUN chmod +x start.sh

# 3. Create a non-root user (Required by Choreo security)
RUN useradd -m choreo
USER choreo

# 4. START THE APP 
CMD ["./start.sh"]