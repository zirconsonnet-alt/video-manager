# B 站视频管理器

一个用 Python 编写的桌面视频管理器，基于 `bilibili-api-python` 获取视频下载信息，支持：

- 只填链接和保存目录
- 粘贴带描述文字的分享内容，自动提取其中的 B 站链接
- 一次提交多个视频链接，自动进入下载队列并逐个下载
- 单链接可展开整个分 P 合集，一次加入完整队列
- 可选跳过同名文件，避免重复下载，会检查保存目录下的子目录
- 选择保存目录
- 单页极简下载界面
- 自动用 `ffmpeg` 合并音视频为 `mp4`

## 运行环境

- Python 3.12+
- `ffmpeg` 已安装并加入系统 `PATH`

## 安装依赖

```powershell
python -m pip install -r requirements.txt
```

## Windows 发布包

如果使用 Releases 里的 Windows 压缩包：

1. 解压 `video-manager-v0.1.0-windows.zip`。
2. 确认 `ffmpeg` 已安装并加入系统 `PATH`。
3. 双击 `VideoManager.exe`。

`app_config.json`、`credential.json`、错误日志和默认下载目录会保存在 `VideoManager.exe` 同目录。

## 启动

优先双击这个无黑框启动器：

```text
start_video_manager_silent.vbs
```

旧入口 `start_video_manager.bat` 现在也会直接转发到无黑框启动器，不再显示 `checking...` 命令提示符。

如果你更习惯 Python 方式，也可以使用隐藏启动：

```powershell
pythonw start_video_manager.pyw
```

也可以直接双击 [start_video_manager.pyw](./start_video_manager.pyw)。

下面这个批处理是调试入口，会经过命令行窗口，建议只在排查依赖问题时使用：

```powershell
start_video_manager_debug.bat
```

## 使用方法

1. 复制一个或多个 B 站链接，或直接复制带描述文字的分享内容，然后在链接输入框里直接粘贴。
2. 如果这是一个多分 P 视频，勾选“单链接时展开整个分P合集”后，只贴 1 个链接也能自动展开整套分 P。
3. 如果目标目录或其子目录里已经有同名视频文件，勾选“不重复下载”后会直接跳过，不再生成 `(2)`、`(3)` 这种重复文件。
4. 选择视频保存目录。
5. 点击“加入下载队列”。
6. 程序会按队列顺序逐个下载，你可以继续追加新的链接。

## 登录态

如果你需要下载更高清晰度或会员可见清晰度，可以在程序目录放一个 `credential.json` 文件。

可以复制 `credential.example.json` 为 `credential.json`，再填入自己的 Cookie 字段：

- `sessdata`
- `bili_jct`
- `buvid3`
- `buvid4`
- `dedeuserid`
- `ac_time_value`

## 说明

- 当前版本主要支持普通 B 站视频链接。
- 多 P 视频会优先按照链接里的 `p=` 参数下载对应分 P；如果没有 `p=`，默认下载第 1 P。
- 勾选“单链接时展开整个分P合集”后，单个多 P 链接会被自动拆成多个队列任务。
- 勾选“不重复下载”后，如果检测到目标目录或子目录里已有同名文件，或当前队列里已存在同名任务，会直接标记为“已跳过”。
- 下载过程中会显示当前任务状态、进度、输出路径，以及完整任务队列。

## 构建发布包

```powershell
.\build_release.ps1 -Version 0.1.0
```

构建产物会生成在 `dist/`：

- `dist/video-manager-v0.1.0-windows/`
- `dist/video-manager-v0.1.0-windows.zip`
