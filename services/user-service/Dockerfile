FROM python:3.12-slim

# Non-root user for security
RUN addgroup --system appgroup && adduser --system --ingroup appgroup appuser

WORKDIR /app

# Install dependencies first (better layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY app/ ./app/

# Ownership & permissions
RUN chown -R appuser:appgroup /app
USER appuser

EXPOSE 8001

# Graceful shutdown via --timeout-graceful-shutdown
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001", \
     "--workers", "1", "--timeout-graceful-shutdown", "30"]
