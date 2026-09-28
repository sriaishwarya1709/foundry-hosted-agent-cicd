FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app/user_agent

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY . .

# Precompile at build time so cold starts are faster and syntax errors fail the build.
RUN python -m compileall -q $(python -c "import sysconfig as s; print(s.get_paths()['purelib'])") || true; \
    python -m compileall -q .

EXPOSE 8088

CMD ["python", "main.py"]
