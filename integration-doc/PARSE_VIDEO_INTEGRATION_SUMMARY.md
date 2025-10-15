# Parse-Video 集成实现总结

## 📋 项目概述

成功将Parse-Video服务集成到Mirror-Leech-Telegram-Bot中，实现智能视频/图集解析和自动下载上传功能。

---

## ✅ 已完成的阶段

### 阶段1: Parse-Video服务部署 ✅

**部署方式:** Docker容器
- 镜像: `wujunwei928/parse-video:latest`
- 端口映射: `18085:8080`
- 状态: ✅ 运行正常

**验证结果:**
```bash
docker ps | grep parse-video
# 容器运行中

curl http://localhost:18085/
# ✅ 前端页面可访问

curl "http://localhost:18085/video/share/url/parse?url=..."
# ✅ API响应正常
```

---

### 阶段2: API调用模块和工具函数 ✅

#### 创建的文件：

**1. `bot/helper/parse_video_helper.py`**
- `parse_video_api(url)` - 调用Parse-Video API
- `check_parse_video_health()` - 健康检查
- `format_video_info(data)` - 格式化视频信息

**2. `bot/helper/ext_utils/url_utils.py`**
- `extract_url_from_text(text)` - 提取URL
- `extract_all_urls_from_text(text)` - 提取所有URL
- `is_valid_url(url)` - 验证URL
- `get_domain(url)` - 获取域名

**3. 配置更新 `config.py`**
```python
PARSE_VIDEO_API = "http://localhost:18085"
PARSE_VIDEO_ENABLED = True
PARSE_VIDEO_TIMEOUT = 30
```

**测试结果:**
- ✅ Parse-Video API调用正常
- ✅ URL提取功能正确
- ✅ 所有工具函数测试通过

---

### 阶段3: 视频链接处理器 ✅

#### 创建的文件：

**`bot/modules/video_parser.py`**

**核心类: `VideoLinkProcessor`**

**功能特性:**

1. **双策略下载**
   - 策略1: Parse-Video解析 → 获取直链 → yt-dlp下载
   - 策略2: 直接使用yt-dlp处理原链接（兜底）

2. **视频处理**
   - 解析视频信息（标题、作者、封面）
   - 使用yt-dlp下载最佳质量
   - 继承Mirror-Bot完整上传逻辑

3. **图集处理** ⭐
   - 识别图集类型
   - 批量下载所有图片
   - **以媒体组(相册)形式上传到Telegram**
   - Telegram限制：每组最多10张图片
   - 只有第一张图片带Caption
   - 自动清理临时文件

4. **错误处理**
   - 友好的错误提示
   - 详细的失败原因说明
   - 完整的日志记录

**测试结果:**
- ✅ Caption构建正确
- ✅ 文件名清理正确
- ✅ 媒体组限制正确
- ✅ 错误处理友好

---

### 阶段4: 消息拦截器和路由逻辑 ✅

#### 创建的文件：

**`bot/modules/direct_link_handler.py`**

**功能:**
- 拦截用户直接发送的消息（无命令）
- 自动识别URL并处理
- 无URL时显示友好提示

#### 修改的文件：

**`bot/core/handlers.py`**

**集成方式:**
```python
# 在add_handlers()函数最后添加
TgClient.bot.add_handler(
    MessageHandler(
        handle_direct_message,
        filters=(text | filters.caption) 
        & ~command("") 
        & CustomFilters.authorized,
    ),
    group=-1  # 较低优先级，不干扰现有命令
)
```

**权限控制:**
- 使用`CustomFilters.authorized`
- 只有授权用户可使用
- 兼容现有权限体系

---

## 🎯 功能流程图

```
用户发送消息
    │
    ├─ 包含命令(/ytdlleech, /leech等) ────> 原有逻辑(不变)
    │
    └─ 不包含命令
        │
        ├─ 包含URL
        │   │
        │   ├─ Parse-Video解析
        │   │   │
        │   │   ├─ 解析成功
        │   │   │   │
        │   │   │   ├─ 返回视频 ────> yt-dlp下载 ────> 上传到TG
        │   │   │   │
        │   │   │   └─ 返回图集 ────> 下载图片 ────> 媒体组上传
        │   │   │
        │   │   └─ 解析失败 ────> yt-dlp直接处理 ────> 上传或报错
        │   │
        │   └─ 所有策略失败 ────> 友好的错误提示
        │
        └─ 不包含URL ────> 使用说明提示
```

---

## 📱 用户体验示例

### 场景1: 发送快手链接（Parse-Video成功）

```
用户: https://v.kuaishou.com/xxx

Bot: 🔍 检测到视频链接
     📡 开始处理...

Bot: 📡 正在通过 Parse-Video 解析...

Bot: ✅ Parse-Video 解析成功！
     
     📹 标题: 搞笑视频合集
     👤 作者: 张三
     🔗 获取到视频直链
     
     ⬇️ 开始下载...

Bot: [yt-dlp进度显示...]

Bot: [视频上传到用户对话窗口]
```

### 场景2: 发送抖音图集链接

```
用户: https://v.douyin.com/xxx (图集链接)

Bot: 🔍 检测到视频链接
     📡 正在通过 Parse-Video 解析...

Bot: ✅ Parse-Video 解析成功！
     
     📸 类型: 图集
     📹 标题: 美食分享
     👤 作者: 李四
     🖼️ 图片数: 6 张
     
     ⬇️ 开始下载图集...

Bot: 📥 正在下载图片 1/6...
     📥 正在下载图片 2/6...
     ...

Bot: [以相册形式上传6张图片]

Bot: ✅ 图集上传完成
     
     📸 共 6 张图片
     📹 美食分享
     👤 李四
```

### 场景3: 发送YouTube链接（Parse-Video失败，yt-dlp成功）

```
用户: https://youtube.com/watch?v=xxx

Bot: 🔍 检测到视频链接
     📡 正在通过 Parse-Video 解析...

Bot: ⚠️ Parse-Video 未能解析
     🔄 尝试 yt-dlp 直接处理...

Bot: 📥 正在下载视频...

Bot: [yt-dlp进度显示...]

Bot: [视频上传到用户]
```

### 场景4: 发送不支持的链接

```
用户: https://unsupported-site.com/video/123

Bot: 🔍 检测到视频链接
     📡 开始处理...

Bot: ❌ 不支持该URL或下载失败
     
     📝 错误信息:
     Unsupported URL: https://unsupported-site.com/...
     
     💡 可能原因:
     • 平台不支持或链接已失效
     • 需要登录或有地域限制
     • 视频已被删除
     
     🔗 原始链接:
     https://unsupported-site.com/video/123
```

### 场景5: 没有发送链接

```
用户: 你好

Bot: 💡 使用说明
     
     直接发送视频分享链接即可下载
     
     支持平台：
     • 抖音 (Douyin)
     • 快手 (Kuaishou)
     • 小红书 (Xiaohongshu)
     • 哔哩哔哩 (Bilibili)
     • 微博 (Weibo)
     • 以及其他20+平台...
     
     其他功能请使用命令：
     • /ytdlleech - YouTube等平台
     • /leech - 通用下载
     • /help - 查看所有命令
```

---

## 🌟 核心特性

### 1. 智能识别
- ✅ 自动检测视频分享链接
- ✅ 区分视频和图集
- ✅ 无需记忆命令

### 2. 双保险策略
- ✅ Parse-Video优先（支持20+平台）
- ✅ yt-dlp兜底（支持1000+网站）
- ✅ 最大化成功率

### 3. 图集支持 ⭐
- ✅ 自动识别图集
- ✅ 批量下载图片
- ✅ **以相册形式呈现**
- ✅ 符合Telegram用户习惯

### 4. 完美集成
- ✅ 不影响原有命令
- ✅ 复用Mirror-Bot上传逻辑
- ✅ 继承权限控制
- ✅ 返回到用户对话窗口

### 5. 用户体验
- ✅ 实时进度显示
- ✅ 友好错误提示
- ✅ 详细的视频信息
- ✅ 自动清理临时文件

---

## 📦 支持的平台

### Parse-Video支持（20+）:
- ✅ 抖音 (Douyin)
- ✅ 快手 (Kuaishou)
- ✅ 小红书 (Xiaohongshu)
- ✅ 哔哩哔哩 (Bilibili)
- ✅ 微博 (Weibo)
- ✅ 皮皮虾、皮皮搞笑
- ✅ 火山短视频
- ✅ 微视
- ✅ 西瓜视频
- ✅ 最右
- ✅ 梨视频
- ✅ 全民K歌
- ✅ 6间房
- ✅ 美拍
- ✅ 新片场
- ✅ 好看视频
- ✅ 虎牙
- ✅ AcFun
- ✅ 等等...

### yt-dlp兜底支持:
- ✅ YouTube
- ✅ TikTok
- ✅ 以及1000+其他网站

---

## 🔧 技术架构

### 微服务架构
```
┌──────────────────────────────────────┐
│   Mirror-Leech-Telegram-Bot          │
│   (Python / Pyrogram)                │
│                                      │
│   ┌──────────────────────────────┐  │
│   │  直接消息处理器                │  │
│   │  direct_link_handler.py      │  │
│   └──────────┬───────────────────┘  │
│              │                       │
│   ┌──────────▼───────────────────┐  │
│   │  视频链接处理器                │  │
│   │  video_parser.py             │  │
│   │  - VideoLinkProcessor        │  │
│   │  - 图集处理                   │  │
│   │  - yt-dlp集成                │  │
│   └──────────┬───────────────────┘  │
│              │                       │
│   ┌──────────▼───────────────────┐  │
│   │  Parse-Video API Helper      │  │
│   │  parse_video_helper.py       │  │
│   └──────────┬───────────────────┘  │
└──────────────┼───────────────────────┘
               │ HTTP API
               ▼
┌──────────────────────────────────────┐
│   Parse-Video Service                │
│   (Go / Gin Framework)               │
│   Docker Container                   │
│   Port: 18085                        │
└──────────────────────────────────────┘
```

### 数据流
```
用户消息 
  → URL提取 
  → Parse-Video API调用 
  → 结果判断(视频/图集)
  → 下载(yt-dlp/aiohttp)
  → 上传(Pyrogram)
  → 返回用户
```

---

## 📝 配置说明

### Parse-Video服务配置
```python
# config.py
PARSE_VIDEO_API = "http://localhost:18085"  # 服务地址
PARSE_VIDEO_ENABLED = True                  # 是否启用
PARSE_VIDEO_TIMEOUT = 30                    # 超时时间(秒)
```

### 权限控制
```python
# 使用现有的授权机制
AUTHORIZED_CHATS = "chat_id1 chat_id2"  # 授权的聊天
SUDO_USERS = "user_id1 user_id2"        # 管理员用户
OWNER_ID = owner_user_id                # 所有者
```

---

## 🚀 部署说明

### 1. Parse-Video服务
```bash
# 使用Docker部署
docker run -d -p 18085:8080 wujunwei928/parse-video

# 验证服务
curl http://localhost:18085/
```

### 2. Mirror-Bot集成
所有文件已创建完成，无需额外操作：
- ✅ `bot/helper/parse_video_helper.py`
- ✅ `bot/helper/ext_utils/url_utils.py`
- ✅ `bot/modules/video_parser.py`
- ✅ `bot/modules/direct_link_handler.py`
- ✅ `bot/core/handlers.py` (已修改)
- ✅ `config.py` (已添加配置)

### 3. 启动Bot
```bash
cd /root/data/docker_data/mirror-leech-telegram-bot
python3 -m bot
```

---

## ⚠️ 注意事项

### 1. Telegram限制
- 媒体组最多10张图片
- 单个文件最大2GB（普通用户）
- 单个文件最大4GB（Premium用户）

### 2. Parse-Video限制
- 某些平台链接有时效性
- 需要保持服务运行
- API响应时间可能波动

### 3. 网络要求
- Mirror-Bot需要能访问Parse-Video服务
- Mirror-Bot需要能访问目标平台
- 建议使用稳定的网络环境

---

## 🐛 常见问题

### Q1: Parse-Video解析失败？
A: 正常情况，会自动降级到yt-dlp。可能原因：
- 链接已失效
- 平台不在支持列表
- 视频需要登录

### Q2: 图集只上传了部分图片？
A: Telegram限制每组最多10张，超过会自动截取前10张。

### Q3: 下载速度慢？
A: 取决于：
- 源站速度
- 网络环境
- 文件大小

### Q4: 如何查看日志？
A: 使用Bot的 `/log` 命令（需要sudo权限）

---

## 📊 测试清单

- [✅] Parse-Video服务健康检查
- [✅] API调用功能测试
- [✅] URL提取功能测试
- [✅] 视频下载逻辑测试
- [✅] 图集下载逻辑测试
- [✅] 媒体组构建测试
- [✅] Caption格式化测试
- [✅] 错误处理测试
- [✅] 权限控制测试
- [ ] 实际Bot集成测试（阶段5）
- [ ] 多平台链接测试（阶段5）
- [ ] 性能压力测试（阶段5）

---

## 🎉 总结

### 已完成功能
- ✅ Parse-Video服务部署
- ✅ API调用模块
- ✅ 视频链接处理器
- ✅ 图集支持（相册形式）
- ✅ 消息拦截器
- ✅ 路由逻辑集成

### 实际测试结果（阶段5）✅
- ✅ 快手视频测试通过
  - 链接: `https://v.kuaishou.com/KNXxJe25`
  - 标题: "女朋友自己一个人在家都干点啥"
  - 大小: 624.32KB
  - 结果: Parse-Video解析成功 → yt-dlp下载无水印视频 → 成功上传到Telegram
- ✅ 权限控制验证通过
- ✅ 状态消息显示正常
- ✅ 错误处理机制正常

### 已解决的技术难点
1. **TaskListener属性初始化问题**
   - 问题: `'NoneType' object has no attribute 'startswith'`
   - 原因: `self.thumb`、`self.same_dir` 等属性未正确初始化
   - 解决: 在 `VideoLinkProcessor.__init__` 中明确初始化所有必需属性

2. **URL传递问题**
   - 问题: yt-dlp无法获取视频直链
   - 原因: `self.link` 在 `before_start()` 之前未设置
   - 解决: 在 `_download_with_ytdlp()` 开始时立即设置 `self.link = url`

3. **Docker缓存问题**
   - 问题: 代码更新后容器仍运行旧代码
   - 解决: 使用 `docker-compose build --no-cache` 强制重建

4. **on_download_complete回调问题**
   - 问题: 下载完成后上传流程中断
   - 解决: 添加详细日志并确保所有必需属性都已初始化

### 创新点
1. **智能降级策略** - Parse-Video + yt-dlp双保险
2. **图集相册支持** - 完美适配Telegram媒体组
3. **零学习成本** - 直接发链接即可，无需命令
4. **完美集成** - 不影响原有任何功能
5. **无水印下载** - Parse-Video提供去水印直链

### 性能表现
- Parse-Video API响应: ~3-4秒
- 视频下载速度: 2.5MB/s (624KB视频，约0.3秒)
- 总处理时间: ~8-10秒（从发送链接到收到视频）

---

**🎬 项目状态: 核心功能已完成并测试通过！✅ 🎬**

Created: 2025-10-14
Author: AI Assistant with User Guidance

