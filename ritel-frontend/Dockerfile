# Streamlit frontend for EDEE (Cloud Run)
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY demo/ ./demo/
# Style chooser pictures (sized copies only; the originals in style_previews_source/ stay in git)
COPY style_previews/ ./style_previews/

EXPOSE 8080

# Cloud Run expects the app on 8080. Append "-- --demo" to run offline on the recorded transcript.
CMD ["streamlit", "run", "app.py", "--server.port=8080", "--server.address=0.0.0.0", "--server.headless=true"]
