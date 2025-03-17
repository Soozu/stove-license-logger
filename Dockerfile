FROM python:3.9-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=8080
ENV HOST=0.0.0.0
ENV DEBUG=False

CMD ["gunicorn", "wsgi:app", "--bind", "0.0.0.0:8080"] 