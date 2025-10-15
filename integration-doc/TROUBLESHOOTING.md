# Parse-Video集成故障排除指南

## 🔍 调试工具和方法

### 1. 查看Bot日志
```bash
# 实时查看日志
docker logs -f mirror-leech-telegram-bot-app-1

# 查看最近100行
docker logs mirror-leech-telegram-bot-app-1 --tail 100

# 搜索特定错误
docker logs mirror-leech-telegram-bot-app-1 2>&1 | grep -E "ERROR|Exception|VideoLinkProcessor"

# 搜索Parse-Video相关日志
docker logs mirror-leech-telegram-bot-app-1 2>&1 | grep -i "parse-video"
```

### 2. 验证服务状态
```bash
# 检查Parse-Video服务
docker ps | grep parse-video
curl http://localhost:18085/

# 检查Bot容器
docker ps | grep mirror-leech-telegram-bot

# 进入容器内部
docker exec -it mirror-leech-telegram-bot-app-1 bash
```

### 3. 验证文件是否正确部署
```bash
# 检查新文件是否在容器中
docker exec mirror-leech-telegram-bot-app-1 ls -la /usr/src/app/bot/modules/ | grep video_parser
docker exec mirror-leech-telegram-bot-app-1 ls -la /usr/src/app/bot/helper/ | grep parse_video

# 检查文件内容
docker exec mirror-leech-telegram-bot-app-1 cat /usr/src/app/bot/helper/parse_video_helper.py | head -20
```

---

## 🐛 常见错误及解决方案

### 错误1: 'NoneType' object has no attribute 'startswith'

#### 完整错误堆栈
```
AttributeError: 'NoneType' object has no attribute 'startswith'
  File "/usr/src/app/bot/modules/video_parser.py", line 52, in on_download_complete
    await super().on_download_complete()
  File "/usr/src/app/bot/helper/listeners/task_listener.py", line 145, in on_download_complete
  File "/usr/src/app/bot/helper/common.py", line 465, in before_start
    if self.thumb != "none" and is_telegram_link(self.thumb):
  File "/usr/src/app/bot/helper/ext_utils/links_utils.py", line 22, in is_telegram_link
    return url.startswith(("https://t.me/", "tg://openmessage?user_id="))
```

#### 症状
- 下载进度显示100%
- 文件已在downloads目录中
- 但没有上传到Telegram
- Bot卡住不响应

#### 根本原因
`VideoLinkProcessor`继承自`TaskListener`，但以下属性未正确初始化：
- `self.thumb` - 被初始化为`None`，但`before_start()`中调用`is_telegram_link(self.thumb)`
- `self.same_dir` - 未初始化，但`on_download_complete()`中检查此属性
- `self.link` - 未在正确时机设置，导致URL传递失败

#### 解决方案
在`VideoLinkProcessor.__init__`中添加以下初始化：

```python
def __init__(self, client, message, url):
    self.message = message
    self.client = client
    self.url = url
    self.status_msg = None
    self.download_path = None
    super().__init__()  # 调用TaskConfig.__init__
    
    # ⚠️ 关键修复：初始化所有必需属性
    if self.name is None:
        self.name = ""
    if self.thumb is None:
        self.thumb = "none"  # 必须是字符串"none"，不能是None
    self.same_dir = None     # on_download_complete()中会检查
    
    # 功能标志
    self.is_leech = True
    self.is_ytdlp = True
```

#### 为什么这些属性很重要？

1. **`self.thumb = "none"`**
   - `TaskConfig.__init__`设置为`None`
   - `before_start()`调用`is_telegram_link(self.thumb)`
   - `is_telegram_link()`内部调用`url.startswith(...)`
   - 如果`url`是`None`，会抛出`AttributeError`
   - 正确值应该是字符串`"none"`（Bot内部约定）

2. **`self.same_dir = None`**
   - 用于多链接/同目录下载功能
   - `on_download_complete()`第99行检查：`if self.folder_name and self.same_dir`
   - 如果属性不存在，会抛出`AttributeError`
   - 单视频下载设为`None`即可

3. **`self.link` 设置时机**
   - `YoutubeDLHelper`从`self._listener.link`读取URL
   - 必须在调用`before_start()`之前设置
   - 在`_download_with_ytdlp()`开始时设置：
   ```python
   async def _download_with_ytdlp(self, url, video_info=None):
       try:
           self.link = url  # ⚠️ 必须先设置
           await self.before_start()
           # ... 其余代码
   ```

---

### 错误2: VideoLinkProcessor object has no attribute 'same_dir'

#### 症状
```
AttributeError: 'VideoLinkProcessor' object has no attribute 'same_dir'
```

#### 原因
`on_download_complete()`方法在第99行检查`self.same_dir`属性，但该属性未在`__init__`中初始化。

#### 解决方案
在`__init__`中添加：
```python
self.same_dir = None
```

---

### 错误3: 下载完成但没有上传

#### 症状
- 日志显示："Download completed: 女朋友自己一个人在家都干点啥.mp4"
- 文件存在于`/usr/src/app/downloads/[message_id]/`
- 但没有"Leech Name"或"Upload"相关日志
- 视频没有发送到Telegram

#### 排查步骤

1. **添加调试日志**
   覆盖`on_download_complete()`方法：
   ```python
   async def on_download_complete(self):
       LOGGER.info(f"VideoLinkProcessor: on_download_complete called for {self.name}")
       try:
           await super().on_download_complete()
           LOGGER.info(f"VideoLinkProcessor: upload completed for {self.name}")
       except Exception as e:
           LOGGER.error(f"VideoLinkProcessor: on_download_complete error: {e}")
           import traceback
           LOGGER.error(traceback.format_exc())
           raise
   ```

2. **查看详细错误**
   ```bash
   docker logs mirror-leech-telegram-bot-app-1 2>&1 | grep -A 20 "VideoLinkProcessor"
   ```

3. **检查task_dict**
   `on_download_complete()`第129-130行会检查：
   ```python
   if self.mid not in task_dict:
       return
   ```
   确保下载任务正确添加到`task_dict`中。

---

### 错误4: Parse-Video API调用失败

#### 症状
- 日志显示："Parse-Video API returned status 500"
- 或："Parse-Video API request failed: Cannot connect to host"

#### 排查步骤

1. **检查Parse-Video服务**
   ```bash
   docker ps | grep parse-video
   docker logs parse-video --tail 50
   ```

2. **测试API连接**
   ```bash
   # 从Bot容器内测试
   docker exec mirror-leech-telegram-bot-app-1 curl http://localhost:18085/
   
   # 测试实际解析
   curl "http://localhost:18085/video/share/url/parse?url=https://v.kuaishou.com/xxx"
   ```

3. **检查网络**
   如果Parse-Video和Bot在不同容器：
   ```bash
   # 检查容器网络
   docker network ls
   docker network inspect bridge
   ```

4. **检查配置**
   编辑`config.py`：
   ```python
   PARSE_VIDEO_API = "http://localhost:18085"  # 或容器名称
   PARSE_VIDEO_ENABLED = True
   PARSE_VIDEO_TIMEOUT = 30
   ```

---

### 错误5: Docker构建缓存问题

#### 症状
- 修改了代码
- 运行了`docker-compose up -d --build`
- 但容器仍运行旧代码
- 新文件不在容器中

#### 原因
Docker使用构建缓存，`COPY . .`步骤可能被缓存，导致新文件未复制。

#### 解决方案

**方法1: 完全清除缓存（推荐）**
```bash
cd /root/data/docker_data/mirror-leech-telegram-bot
docker-compose down
docker-compose build --no-cache
docker-compose up -d
```

**方法2: 强制重建**
```bash
docker-compose down
docker-compose up -d --build --force-recreate
```

**方法3: 清除所有构建缓存（慎用）**
```bash
docker builder prune -a
```

#### 验证
```bash
# 检查镜像创建时间（应该是刚才）
docker images | grep mirror-leech-telegram-bot

# 验证文件在容器中
docker exec mirror-leech-telegram-bot-app-1 ls -la /usr/src/app/bot/modules/ | grep video_parser
```

---

### 错误6: 权限问题 - "您没有权限使用此功能"

#### 症状
发送视频链接后收到：
```
⚠️ 您没有权限使用此功能。
请联系管理员授权。
```

#### 原因
用户ID不在授权列表中。

#### 解决方案

编辑`config.py`，添加用户ID：

```python
# 方式1: 设置OWNER_ID
OWNER_ID = 1861667385  # 您的Telegram用户ID

# 方式2: 添加到SUDO_USERS
SUDO_USERS = "1861667385 1234567890"  # 空格分隔多个ID

# 方式3: 添加到AUTHORIZED_CHATS
AUTHORIZED_CHATS = "1861667385"  # 允许特定用户或群组

# 如果都不设置，所有用户都能使用（不推荐）
```

如何获取您的Telegram用户ID：
1. 发送消息给Bot: `/id`
2. 或使用 @userinfobot
3. 或查看Bot日志中的用户ID

---

### 错误7: 消息没有被处理

#### 症状
- 发送视频链接
- Bot没有任何响应
- 日志中没有"Direct link detected"消息

#### 可能原因及解决方案

1. **handlers.py未正确修改**
   检查是否添加了直接消息处理器：
   ```python
   TgClient.bot.add_handler(
       MessageHandler(
           handle_direct_message,
           filters=(text | filters.caption)
           & ~command("")  # 排除命令消息
           & CustomFilters.authorized,
       ),
       group=-1  # 低优先级
   )
   ```

2. **导入缺失**
   检查`handlers.py`顶部：
   ```python
   from ..modules.direct_link_handler import handle_direct_message
   ```

3. **消息被其他处理器拦截**
   确保`group=-1`（低优先级），让现有命令优先处理。

4. **Bot未正确重启**
   ```bash
   docker-compose restart
   # 查看启动日志
   docker logs mirror-leech-telegram-bot-app-1 --tail 50
   ```

---

## 🔧 调试技巧

### 1. 启用详细日志

在`video_parser.py`中添加更多日志：

```python
LOGGER.info(f"Step 1: URL detected: {self.url}")
LOGGER.info(f"Step 2: Calling parse_video_api")
LOGGER.info(f"Step 3: Parse result: {parse_result}")
LOGGER.info(f"Step 4: Starting download with URL: {video_direct_url}")
```

### 2. 使用Python调试器

在容器内安装pdb：
```bash
docker exec -it mirror-leech-telegram-bot-app-1 bash
pip install ipdb

# 在代码中添加断点
import ipdb; ipdb.set_trace()
```

### 3. 测试单个模块

创建测试脚本`/root/data/test/test_integration.py`：
```python
import asyncio
from bot.helper.parse_video_helper import parse_video_api

async def test():
    result = await parse_video_api("https://v.kuaishou.com/KNXxJe25")
    print(result)

asyncio.run(test())
```

### 4. 检查Python导入

在容器内测试导入：
```bash
docker exec -it mirror-leech-telegram-bot-app-1 python3
>>> from bot.helper.parse_video_helper import parse_video_api
>>> from bot.modules.video_parser import VideoLinkProcessor
>>> # 如果没有报错，说明模块存在
```

---

## 📊 成功案例参考

### 快手视频下载示例

**输入:** `https://v.kuaishou.com/KNXxJe25`

**预期日志流程:**
```
1. Direct link detected from user 1861667385: https://v.kuaishou.com/KNXxJe25...
2. Parse-Video API success: 女朋友自己一个人在家都干点啥
3. Parse-Video success: 女朋友自己一个人在家都干点啥
4. Download with YT_DLP: 女朋友自己一个人在家都干点啥.mp4
5. VideoLinkProcessor: on_download_complete called for 女朋友自己一个人在家都干点啥.mp4
6. Download completed: 女朋友自己一个人在家都干点啥.mp4
7. Leech Name: 女朋友自己一个人在家都干点啥.mp4
8. VideoLinkProcessor: upload completed for 女朋友自己一个人在家都干点啥.mp4
```

**预期结果:**
- 视频文件上传到Telegram
- 文件名正确
- 无水印
- 大小: ~624KB

---

## 🆘 获取帮助

如果以上方法都无法解决问题：

1. **收集完整日志**
   ```bash
   docker logs mirror-leech-telegram-bot-app-1 > /tmp/bot_logs.txt
   docker logs parse-video > /tmp/parse_video_logs.txt
   ```

2. **检查系统信息**
   ```bash
   docker --version
   docker-compose --version
   python3 --version
   uname -a
   ```

3. **提供详细错误信息**
   - 完整的错误堆栈
   - 复现步骤
   - 测试的URL
   - 系统环境

4. **查看文档**
   - `/root/data/test/INTEGRATION_GUIDE.md` - 部署指南
   - `/root/data/test/PARSE_VIDEO_INTEGRATION_SUMMARY.md` - 功能总结
   - `/root/data/test/TEST_CHECKLIST.md` - 测试清单

---

**文档版本:** 1.0  
**最后更新:** 2025-10-14  
**测试状态:** ✅ 已验证通过

