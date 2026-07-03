FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 50051

# config.yaml 已 gitignore: 构建前 cp config.example.yaml config.yaml,
# 或运行时挂载: docker run -v ./config.yaml:/app/config.yaml
CMD ["python", "main.py", "--conf", "config.yaml"]
