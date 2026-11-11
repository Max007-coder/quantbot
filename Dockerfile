FROM python:3.11-slim

WORKDIR /app
COPY requirements-linux.txt .
RUN pip install --no-cache-dir -r requirements-linux.txt

COPY . .
RUN mkdir -p data logs

ENV PYTHONUNBUFFERED=1
EXPOSE 8081 8082
CMD ["python", "quantbot.py"]