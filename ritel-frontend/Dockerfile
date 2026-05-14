# Use a lightweight Python image
FROM python:3.9-slim

# Set working directory
WORKDIR /app

# Copy files
COPY requirements.txt .
COPY app.py .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Expose the port Streamlit runs on
EXPOSE 8080

# Run Streamlit
# We map the server port to 8080 because that is what Cloud Run expects
CMD ["streamlit", "run", "app.py", "--server.port=8080", "--server.address=0.0.0.0"]
