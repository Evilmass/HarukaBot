FROM mcr.microsoft.com/playwright/python:v1.22.0-focal

# https://playwright.net.cn/python/docs/browsers#managing-browser-binaries
ENV TZ=Asia/Shanghai \
    LANG=zh_CN.UTF-8 \
    HOST=0.0.0.0 \
    PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright/ \
    PLAYWRIGHT_SKIP_BROWSER_GC=1

EXPOSE 7070

COPY requirements.txt /tmp/

# cache pip
RUN pip install --no-cache-dir -r /tmp/requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

WORKDIR /haruka_bot

COPY . .

# CMD ["python" ,"bot.py"]
CMD ["sleep" ,"infinity"]
