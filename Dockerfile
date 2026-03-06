FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend
COPY backend/ /app/backend/

# Copy frontend
COPY frontend/ /app/frontend/

# Data volume for SQLite
RUN mkdir -p /data

EXPOSE 3000

WORKDIR /app/backend
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "3000"]
