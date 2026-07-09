FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ app/
COPY config.py run.py ./

EXPOSE 5001

CMD ["python", "run.py"]
