
FROM python:3.11-slim


RUN apt-get update && apt-get install -y \
    g++ \
    default-jdk-headless \
    && rm -rf /var/lib/apt/lists/*

# Set working directory inside container
WORKDIR /app

# Copy dependency list and install Python packages
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy all project files into the container
COPY . .

# Expose port (Render sets $PORT dynamically, defaults to 8000)
ENV PORT=8000
EXPOSE 8000

# Start Uvicorn pointing to main:app
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT}"]