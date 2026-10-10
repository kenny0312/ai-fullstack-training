FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    CONTRACT_DB_PATH=/data/reviews.sqlite3

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# 服务用普通用户 app 运行，不用 root。
RUN useradd --system --uid 10001 --no-create-home app \
    && mkdir -p /data && chown app:app /data

# 数据库放在命名卷里，删除容器不会丢失审查记录。
VOLUME ["/data"]
EXPOSE 8000
# 启动时先把数据卷交给 app（兼容旧版本以 root 身份创建的卷），再降权启动服务。
CMD ["sh", "-c", "chown -R app:app /data && exec setpriv --reuid=app --regid=app --init-groups uvicorn main:app --host 0.0.0.0 --port 8000"]
