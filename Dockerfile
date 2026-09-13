# Official Playwright image ships with Chromium + all OS-level deps
# already installed, so there's no separate `playwright install` step
# (and no risk of missing system libraries) inside the container.
FROM mcr.microsoft.com/playwright/python:v1.47.0-jammy

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENTRYPOINT ["python", "main.py"]
CMD ["--domains", "postman.com", "supabase.com", "vapi.ai", "--output", "output.json"]
