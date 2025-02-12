FROM anasty17/mltb:latest

# 自动创建目录并设置权限
WORKDIR /usr/src/app
RUN chmod 777 /usr/src/app

# 优先处理依赖（利用缓存）
COPY requirements.txt .
RUN python3 -m venv mltbenv && \
    mltbenv/bin/pip install --no-cache-dir -r requirements.txt

# 最后复制代码（避免缓存干扰）
COPY . .

# 添加验证步骤（查看文件内容）
RUN ls -l && \
    cat /usr/src/app/bot/modules/services.py

CMD ["bash", "start.sh"]