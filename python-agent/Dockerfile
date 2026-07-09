FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN python -m playwright install chromium --with-deps
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-noto-cjk fontconfig \
    && rm -rf /var/lib/apt/lists/*

COPY . .

EXPOSE 50051

# config.yaml 已 gitignore: 构建前 cp config.example.yaml config.yaml,
# 或运行时挂载: docker run -v ./config.yaml:/app/config.yaml
CMD ["python", "main.py", "--conf", "config.yaml"]
