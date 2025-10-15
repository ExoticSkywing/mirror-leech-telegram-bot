# 文件管理机制说明

## 📁 临时文件存储位置

所有下载的文件都存储在容器内的 `/usr/src/app/downloads/` 目录中。

### 文件路径结构

```
/usr/src/app/downloads/
├── {message_id}              # 视频下载目录（由YoutubeDLHelper创建）
│   └── video_file.mp4
└── {message_id}_gallery      # 图集下载目录（由VideoLinkProcessor创建）
    ├── temp_000.webp
    ├── temp_001.webp
    ├── image_000.jpg         # 转换后的JPG文件
    └── image_001.jpg
```

## 🔄 文件清理机制

### 1. **视频文件清理**（由 mirror-bot 原有逻辑管理）

视频下载使用 `YoutubeDLHelper` + `TaskListener`，遵循 mirror-bot 的原有清理机制：

#### ✅ 成功上传后的清理
- **触发点**：`TaskListener.on_upload_complete()`
- **清理对象**：
  - 下载目录：`/usr/src/app/downloads/{message_id}/`
  - 缩略图文件：`self.thumb`（如果存在）
- **清理方式**：调用 `clean_target(self.up_dir)`
- **时机**：
  - 如果 `self.seed = True`（做种），上传完成后立即清理
  - 如果 `self.seed = False`，保留文件直到所有任务完成

#### ❌ 下载/上传失败后的清理
- **触发点**：
  - `TaskListener.on_download_error(error)`
  - `TaskListener.on_upload_error(error)`
- **清理对象**：
  - 调用 `self.remove_from_same_dir()` 移除相关任务
  - 如果有缩略图，删除缩略图文件
- **清理方式**：从 `task_dict` 中移除任务引用

#### 📝 视频文件清理代码位置
```python
# 文件: bot/helper/listeners/task_listener.py

async def on_upload_complete(...):
    # 第376-379行：上传成功后清理
    if self.seed:
        await clean_target(self.up_dir)  # 清理整个上传目录
        
async def on_download_error(self, error):
    # 第403-409行：下载失败后清理
    await self.remove_from_same_dir()  # 移除同目录任务
    
async def on_upload_error(self, error):
    # 第441-449行：上传失败后清理
    await self.remove_from_same_dir()
    await clean_download(self.dir)  # 清理下载目录
```

### 2. **图集文件清理**（由新增策略管理）

图集下载由 `VideoLinkProcessor._handle_image_gallery()` 管理，使用**独立的清理机制**：

#### ✅ 成功上传后的清理
- **触发点**：图集上传到 Telegram 成功后
- **清理对象**：整个临时目录 `/usr/src/app/downloads/{message_id}_gallery/`
- **清理方式**：
  ```python
  # 第444行
  await self._cleanup_temp_files(temp_dir)
  
  # 调用 clean_target() 删除整个目录及其内容
  async def _cleanup_temp_files(self, directory):
      await clean_target(directory)
  ```
- **时机**：立即清理，不等待其他任务

#### ❌ 下载/上传失败后的清理
- **触发点**：异常处理块 `except Exception as e:`
- **清理对象**：临时目录（如果存在）
- **清理方式**：
  ```python
  # 第450-452行
  if await aiopath.exists(temp_dir):
      await self._cleanup_temp_files(temp_dir)
  ```
- **保证**：即使失败也会清理所有临时文件

#### 📝 图集清理代码位置
```python
# 文件: bot/modules/video_parser.py

async def _handle_image_gallery(self, images_list, video_info):
    try:
        # 创建临时目录
        temp_dir = f"{DOWNLOAD_DIR}{self.mid}_gallery"
        await makedirs(temp_dir, exist_ok=True)
        
        # ... 下载和上传逻辑 ...
        
        # 成功后清理（第444行）
        await self._cleanup_temp_files(temp_dir)
        
    except Exception as e:
        # 失败后清理（第450-452行）
        if await aiopath.exists(temp_dir):
            await self._cleanup_temp_files(temp_dir)
```

### 3. **中间临时文件清理**

在图集处理过程中，会产生以下临时文件：

#### WebP 原始文件
- **文件名**：`temp_{idx:03d}.webp`
- **用途**：yt-dlp 下载的原始 WebP 格式图片
- **清理时机**：转换为 JPG 后立即删除
  ```python
  # 第340-342行
  try:
      await aioremove(temp_file)  # 删除 WebP 临时文件
  except:
      pass
  ```

#### JPG 转换文件
- **文件名**：`image_{idx:03d}.jpg`
- **用途**：用于上传到 Telegram 的 JPG 格式图片
- **清理时机**：整个图集上传完成后，随目录一起删除

## 🛡️ 清理保障机制

### 1. 异常安全（Exception Safety）
所有清理操作都包含在 `try-except` 块中，确保：
- 清理失败不会影响用户体验
- 错误会被记录到日志中
- 不会因为清理失败而崩溃

```python
async def _cleanup_temp_files(self, directory):
    try:
        await clean_target(directory)
        LOGGER.info(f"Cleaned up temp directory: {directory}")
    except Exception as e:
        LOGGER.error(f"Error cleaning up temp directory: {e}")
```

### 2. 双重保障
- **成功路径**：上传成功后主动清理
- **失败路径**：异常处理中清理
- **结果**：无论成功还是失败，临时文件都会被删除

### 3. 目录级清理
使用 `clean_target()` 递归删除整个目录：
- 删除目录内所有文件
- 删除所有子目录
- 删除目录本身

## 📊 清理验证

### 检查是否有残留文件

```bash
# 进入容器检查
docker exec mirror-leech-telegram-bot-app-1 ls -lh /usr/src/app/downloads/

# 应该显示：total 0（表示目录为空）
```

### 查看清理日志

```bash
# 查看清理相关日志
docker logs mirror-leech-telegram-bot-app-1 2>&1 | grep -i "clean"

# 应该看到类似：
# INFO - Cleaned up temp directory: /usr/src/app/downloads/3091_gallery
```

## 🔍 清理流程对比

| 项目 | 视频下载 | 图集下载 |
|------|---------|---------|
| **管理者** | Mirror Bot 原有逻辑 (`TaskListener`) | 新增策略 (`VideoLinkProcessor`) |
| **存储路径** | `{DOWNLOAD_DIR}{mid}/` | `{DOWNLOAD_DIR}{mid}_gallery/` |
| **成功清理** | `on_upload_complete()` | 上传后立即调用 `_cleanup_temp_files()` |
| **失败清理** | `on_download_error()` / `on_upload_error()` | `except` 块中调用 `_cleanup_temp_files()` |
| **清理时机** | 根据 `self.seed` 决定 | 立即清理 |
| **清理方式** | `clean_target(self.up_dir)` | `clean_target(temp_dir)` |
| **依赖关系** | 可能等待其他任务（多链接） | 独立，不等待其他任务 |

## ✅ 总结

1. **视频文件**：由 mirror-bot 原有的 `TaskListener` 管理，遵循其完善的清理机制
2. **图集文件**：由新增的 `VideoLinkProcessor` 独立管理，确保立即清理
3. **临时文件**：在转换过程中产生，转换后立即删除
4. **清理保障**：双重保障（成功+失败路径），异常安全
5. **验证结果**：实际测试显示 `downloads/` 目录为空，无残留文件

**结论**：两种清理机制相互独立，互不干扰，都能确保临时文件被及时清理，不会产生残留。

