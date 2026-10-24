FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
 && useradd --create-home sim
COPY devices.py .
USER sim
CMD ["python", "devices.py", "--scenario", "normal"]
