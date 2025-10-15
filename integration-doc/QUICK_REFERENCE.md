# Parse-Video集成快速参考

## 🚀 一键部署

```bash
# 1. 启动Parse-Video
docker run -d --name parse-video --restart unless-stopped -p 18085:8080 wujunwei928/parse-video

# 2. 配置Bot (编辑config.py)
PARSE_VIDEO_API = "http://localhost:18085"
PARSE_VIDEO_ENABLED = True
PARSE_VIDEO_TIMEOUT = 30

# 3. 重启Bot
cd /root/data/docker_data/mirror-leech-telegram-bot
docker-compose down
docker-compose up -d --build
```

---

## 📝 核心文件清单

| 文件路径 | 作用 | 状态 |
|---------|------|------|
| `bot/helper/parse_video_helper.py` | Parse-Video API调用 | ✅ 新建 |
| `bot/helper/ext_utils/url_utils.py` | URL检测提取 | ✅ 新建 |
| `bot/modules/video_parser.py` | 视频处理核心逻辑 | ✅ 新建 |
| `bot/modules/direct_link_handler.py` | 消息拦截路由 | ✅ 新建 |
| `bot/core/handlers.py` | 添加直接消息处理器 | ✅ 修改 |
| `config.py` | Parse-Video配置 | ✅ 修改 |

---

## 🔧 关键代码片段

### VideoLinkProcessor初始化（必须）
```python
def __init__(self, client, message, url):
    self.message = message
    self.client = client
    self.url = url
    self.status_msg = None
    self.download_path = None
    super().__init__()
    
    # ⚠️ 关键：必须初始化这些属性
    if self.name is None:
        self.name = ""
    if self.thumb is None:
        self.thumb = "none"  # 必须是"none"字符串
    self.same_dir = None      # 防止AttributeError
    
    self.is_leech = True
    self.is_ytdlp = True
```

### 下载方法（必须先设置self.link）
```python
async def _download_with_ytdlp(self, url, video_info=None):
    try:
        self.link = url  # ⚠️ 必须先设置
        await self.before_start()
        # ... 其余代码
```

### handlers.py添加处理器
```python
from ..modules.direct_link_handler import handle_direct_message

# 在add_handlers()函数末尾添加
TgClient.bot.add_handler(
    MessageHandler(
        handle_direct_message,
        filters=(text | filters.caption) & ~command("") & CustomFilters.authorized,
    ),
    group=-1
)
```

---

## 🐛 常见错误速查

| 错误 | 原因 | 解决 |
|------|------|------|
| `'NoneType' object has no attribute 'startswith'` | `self.thumb`是`None` | 设为`"none"` |
| `'VideoLinkProcessor' object has no attribute 'same_dir'` | 属性未初始化 | 添加`self.same_dir = None` |
| 下载完成但不上传 | `on_download_complete()`异常 | 检查所有属性初始化 |
| 新文件不在容器中 | Docker缓存 | `docker-compose build --no-cache` |
| Parse-Video连接失败 | 服务未启动 | `docker ps \| grep parse-video` |
| 无权限使用 | 用户未授权 | 设置`OWNER_ID`或`SUDO_USERS` |

---

## 📊 日志关键字

### 成功流程
```
Direct link detected → Parse-Video API success → Download with YT_DLP → 
on_download_complete called → Download completed → Leech Name → upload completed
```

### 查看日志
```bash
# 实时日志
docker logs -f mirror-leech-telegram-bot-app-1

# 搜索错误
docker logs mirror-leech-telegram-bot-app-1 2>&1 | grep -E "ERROR|Exception"

# Parse-Video相关
docker logs mirror-leech-telegram-bot-app-1 2>&1 | grep -i "parse-video"
```

---

## ✅ 测试检查清单

- [ ] Parse-Video服务运行中 (`docker ps | grep parse-video`)
- [ ] Parse-Video API可访问 (`curl http://localhost:18085/`)
- [ ] Bot配置正确 (`config.py`中的`PARSE_VIDEO_*`设置)
- [ ] 新文件在容器中 (`docker exec ... ls -la bot/modules/ | grep video_parser`)
- [ ] handlers.py已修改（包含`handle_direct_message`）
- [ ] 用户已授权（`OWNER_ID`或`SUDO_USERS`）
- [ ] Docker镜像是最新的（使用`--no-cache`重建）

---

## 🎯 快速验证

```bash
# 1. 检查所有服务
docker ps | grep -E "parse-video|mirror-leech"

# 2. 测试Parse-Video
curl "http://localhost:18085/video/share/url/parse?url=https://v.kuaishou.com/xxx"

# 3. 验证文件
docker exec mirror-leech-telegram-bot-app-1 ls -la /usr/src/app/bot/modules/video_parser.py

# 4. 查看最近日志
docker logs mirror-leech-telegram-bot-app-1 --tail 50

# 5. 发送测试链接到Bot
# https://v.kuaishou.com/KNXxJe25
```

---

## 🆘 紧急修复

### Bot不响应
```bash
docker-compose restart
docker logs -f mirror-leech-telegram-bot-app-1
```

### 代码更新不生效
```bash
docker-compose down
docker-compose build --no-cache
docker-compose up -d
```

### Parse-Video崩溃
```bash
docker restart parse-video
docker logs parse-video --tail 50
```

### 完全重置
```bash
# ⚠️ 慎用：删除所有数据
docker-compose down -v
docker-compose up -d --build --force-recreate
```

---

## 📚 完整文档

- **部署指南**: `/root/data/test/INTEGRATION_GUIDE.md`
- **功能总结**: `/root/data/test/PARSE_VIDEO_INTEGRATION_SUMMARY.md`
- **故障排除**: `/root/data/test/TROUBLESHOOTING.md`
- **测试清单**: `/root/data/test/TEST_CHECKLIST.md`

---

## 🎉 已验证平台

| 平台 | 状态 | 功能 | 备注 |
|------|------|------|------|
| 快手 | ✅ | 视频下载 | 无水印 |
| 抖音 | 🔄 | 待测试 | 理论支持 |
| 小红书 | 🔄 | 待测试 | 理论支持 |
| B站 | 🔄 | 待测试 | 理论支持 |

---

**版本:** 1.0  
**状态:** ✅ 生产就绪  
**最后测试:** 2025-10-14  
**测试视频:** 女朋友自己一个人在家都干点啥.mp4 (624KB)

