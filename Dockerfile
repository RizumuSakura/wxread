FROM python:3.10-slim

# 设置工作目录
WORKDIR /app

# 设置时区为中国时区
ENV TZ=Asia/Shanghai
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

ENV PATH="/usr/local/bin:${PATH}"

# 复制项目文件
COPY main.py push.py config.py log_utils.py scheduler.py cookie_store.py weread_session.py login_store.py weread_auth.py weread_shelf.py cloud_crypto.py book_context.py reader_protocol.py actions_login.py qr_viewer.html requirements.txt ./

# 安装 Python 依赖
RUN python -m pip install --no-cache-dir -r requirements.txt

# 启动命令
CMD ["python", "scheduler.py"]
