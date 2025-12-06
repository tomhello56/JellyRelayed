FROM python:3.9-slim

WORKDIR /app

RUN addgroup --gid 1000 appuser && adduser --uid 1000 --ingroup appuser --no-create-home appuser

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy all files (including templates folder)
COPY . .

# Create volume mount points and then set ownership
VOLUME /data
VOLUME /media
RUN mkdir -p /data /media
RUN chown -R appuser:appuser /data /media

# Expose Web Port
EXPOSE 5000

CMD ["python", "run.py"]