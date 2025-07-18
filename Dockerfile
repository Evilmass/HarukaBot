FROM mcr.microsoft.com/playwright/python:v1.49.0-noble

ENV TZ=Asia/Shanghai \
    LANG=zh_CN.UTF-8 \
    HOST=0.0.0.0

EXPOSE 8080

# requirements
WORKDIR /tmp

COPY requirements.txt .

RUN pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/ && \
    pip install --no-cache-dir -r /tmp/requirements.txt
RUN export PLAYWRIGHT_DOWNLOAD_HOST=https://registry.npmmirror.com/-/binary/playwright && playwright install chromium

# run
WORKDIR /app
# ENTRYPOINT ["nohup", "python", "bot.py", ">run.log", "2>&1", "&"]
CMD ["sleep", "infinity"]