FROM python:3.11-slim

ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN python -m playwright install chromium --with-deps
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-noto-cjk fonts-inter fontconfig \
    && chmod -R a+rX /ms-playwright \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --system --gid 10001 deck \
    && useradd --system --uid 10001 --gid deck --create-home deck \
    && mkdir -p /app/artifacts \
    && chown -R deck:deck /app

COPY --chown=deck:deck . .

USER deck

EXPOSE 50051

# config.yaml/.env are deliberately excluded from the build context. Mount or
# inject runtime configuration instead of baking credentials into the image.
CMD ["python", "main.py", "--conf", "config.yaml"]
