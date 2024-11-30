FROM mcr.microsoft.com/playwright/python:v1.22.0-focal

ENV TZ=Asia/Shanghai LANG=zh_CN.UTF-8 HOST=0.0.0.0 PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright/

EXPOSE 7070

COPY download_browser.py haruka_bot-1.6.0.post5-py3-none-any.whl /tmp/

RUN sed -i s@/archive.ubuntu.com/@/mirrors.aliyun.com/@g /etc/apt/sources.list && \
    sed -i s@/security.ubuntu.com/@/mirrors.aliyun.com/@g /etc/apt/sources.list && \
    playwright install-deps && \
    python /tmp/download_browser.py install && \
    apt-get clean autoclean && \
    apt-get autoremove --yes && \
    rm -rf /var/lib/{apt,dpkg,cache,log}/

# cache pip
RUN pip install --no-cache-dir /tmp/haruka_bot-1.6.0.post5-py3-none-any.whl -i https://pypi.tuna.tsinghua.edu.cn/simple

WORKDIR /haruka_bot

COPY . .

# CMD ["python" ,"bot.py"]s
CMD ["sleep" ,"infinity"]
