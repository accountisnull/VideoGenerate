# 新机器安装与运行

适用于 **Windows 10/11 x64、PowerShell 5.1 或更高版本**。当前 Worker 使用 Windows 文件锁，启停脚本也面向 Windows；本版不声明支持 macOS、Linux 或 Windows ARM。

安装后可运行环境自检页面，验证前端、API、SQLite、独立 Worker 和实际 MP4 合成。自检不需要 GPU、模型权重、百炼密钥或抖音授权。真实数字人生成和抖音发布仍需另行接入。

## 1. 准备系统工具

| 工具 | 要求与获取方式 |
| --- | --- |
| Git | 从 <https://git-scm.com/downloads/win> 安装；拿到完整源码压缩包时可不安装 |
| Node.js 与 npm | **Node.js 22 x64，至少 22.12.0**，附带 npm 10–12；从 <https://nodejs.org/download/release/latest-v22.x/> 获取 x64 MSI，暂不选其他主版本 |
| uv | 按 <https://docs.astral.sh/uv/getting-started/installation/> 安装；项目安装脚本会通过 uv 准备 Python 3.11，无需预先手工安装 Python |
| FFmpeg 与 ffprobe | 从 <https://ffmpeg.org/download.html#build-windows> 指向的 Windows 构建获取完整发行包，例如 gyan.dev 的 release essentials ZIP；解压并保留整个目录，找到其中的 `bin/` |

uv 官方 PowerShell 安装命令：

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

装完后重新打开 PowerShell，检查工具：

```powershell
node --version
npm.cmd --version
uv --version
git --version
```

FFmpeg 不必加入系统 PATH，下面通过参数指定目录。不要只复制两个 EXE 而丢失发行包需要的 DLL。首次安装需要联网访问 GitHub、PyPI、npm registry 及 Python 下载地址；公司代理环境应使用正常的代理和证书配置，不要关闭证书验证。

## 2. 获取当前源码

```powershell
git clone https://github.com/accountisnull/VideoGenerate.git
Set-Location VideoGenerate
```

私有仓库需要访问权限。也可解压包含当前变更的源码压缩包并进入根目录。源码必须包含 `scripts/setup.ps1`、`scripts/verify.ps1`、`backend/uv.lock` 和 `frontend/package-lock.json`；若缺失，说明版本较旧，应先由维护者提交并推送当前版本。

不要从旧电脑复制 `.venv`、`node_modules`、`frontend/dist`、运行数据库或 `.env`。它们需在本机重新建立，旧电脑的 FFmpeg 路径不能直接复用。

## 3. 安装、构建并检查

以下命令从根目录运行，替换为这台电脑的实际 FFmpeg `bin` 目录：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/setup.ps1 -FfmpegDirectory "C:\Tools\ffmpeg\bin"
```

两个程序都已在 PATH 中时可省略 `-FfmpegDirectory`。已有有效本机配置时会保留并使用；传入新目录才更新两个工具路径。

脚本依次检查系统工具，按锁文件创建 `backend/.venv` 并安装依赖，执行 `npm ci` 和前端构建，检查依赖导入、SQLite、数据目录写入及媒体工具，并实际合成、检测测试 MP4。失败返回非零状态，不继续报告安装成功。

默认安装集成端与开发依赖。第 1 人需要 deepagents 和百炼适配依赖时增加 `-WithContent`：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/setup.ps1 -WithContent -FfmpegDirectory "C:\Tools\ffmpeg\bin"
```

重新安装前先停止项目服务。脚本不升级锁文件，`npm ci` 会重建项目的 `node_modules`。保留可选内容组需在重新安装时继续传 `-WithContent`。

安装脚本会在项目根目录缺少 `.env` 时从 `.env.example` 创建它。也可手动复制并填写：

```dotenv
FFMPEG_PATH='C:/Tools/ffmpeg/bin/ffmpeg.exe'
FFPROBE_PATH='C:/Tools/ffmpeg/bin/ffprobe.exe'
VIDEO_DATA_DIR=data
```

媒体工具路径支持绝对路径、相对**项目根目录**的路径或 PATH 中的程序名。Windows 路径推荐 `/`，含空格时加单引号。配置优先级为系统环境变量 > 根目录 `.env` > 默认值（`ffmpeg`、`ffprobe`、`data`）。本机 `.env` 不提交 Git，修改后重启服务。

`VIDEO_DATA_DIR` 控制数据库和媒体产物目录，支持绝对路径或相对项目根目录的路径；启停脚本的日志与进程记录仍放在根目录 `data/`。旧版 `config/runtime.local.json` 不再读取，其中 `ffmpeg`、`ffprobe` 分别迁移为 `FFMPEG_PATH`、`FFPROBE_PATH`。

### 文本模型配置

根目录 `.env` 已预留写稿、检查和改稿三组配置：

| 步骤 | 变量前缀 |
| --- | --- |
| 写稿 | `TEXT_WRITING_` |
| 检查 | `TEXT_REVIEW_` |
| 改稿 | `TEXT_REVISION_` |

每组都需要填写 `BASE_URL`（对应地域的百炼兼容接口地址）和 `MODEL`（实际模型 ID）。`API_KEY_ENV` 是保存密钥的变量名，默认三组均引用同一份 `DASHSCOPE_API_KEY`；将真实密钥填入 `.env` 的 `DASHSCOPE_API_KEY=` 后即可。各组可以使用相同或不同的模型与接口，也可把 `API_KEY_ENV` 改为另一个在 `.env` 或系统环境中定义的密钥变量名。

`TIMEOUT_SECONDS` 默认为 `60`；`GENERATION_OPTIONS` 是单引号包裹的 JSON 对象，默认 `'{}'`。例如模型支持时可填写 `TEXT_WRITING_GENERATION_OPTIONS='{"temperature": 0.7}'`。系统环境变量优先于 `.env`，不要把真实密钥填入 `.env.example`。

内容生成已提供模型适配，启用与启动步骤见 [内容服务](content-service.md)。模型 ID、地址和密钥留空不影响现有媒体自检；调用模型配置读取函数时会提示缺少的变量名。配音和数字人模型由独立服务负责选型。

## 4. 启动并验证

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/start.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/verify.ps1
```

验证脚本检查 API、Worker、网页，以及 HTTP 提交自检、MP4 下载和视频 Range 请求。成功输出包含 `"verification": "passed"`；`production_ready: false` 表示真实模型与发布尚未接入，是当前版本的预期状态。

打开 <http://127.0.0.1:8765>，四项环境状态应就绪，测试片可播放。自检图案和测试音不是真实人物视频，不会上传抖音。

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/status.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/stop.ps1
```

端口占用时，启动和验证使用相同的新端口：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/start.ps1 -Port 8766
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/verify.ps1 -Port 8766
```

状态和停止脚本自动读取端口与进程标识。前端构建由 API 提供，不需要另开 Vite；关闭浏览器不停止后台。服务只绑定本机回环地址，无需开放入站网络端口。

## 5. 故障处理

| 现象 | 处理 |
| --- | --- |
| 找不到 uv、node、npm.cmd | 安装前置工具后重新打开 PowerShell，检查版本和 PATH |
| Node 版本检查失败 | 使用 Node.js 22 x64，至少 22.12.0；切换后重跑 setup |
| 下载失败 | 检查 PyPI、npm、GitHub 网络／代理，保留锁文件并重跑 |
| 找不到 FFmpeg／ffprobe | `-FfmpegDirectory` 指向同时含两者的目录，更新旧电脑的配置路径 |
| 缺少 DLL、编码器或合成失败 | 使用完整 FFmpeg 发行包，查看安装脚本实际媒体验证的错误输出 |
| 配置为空或路径无效 | 按 `.env.example` 检查变量名、引号与路径，工具路径不能留空 |
| 受管进程仍在运行 | 先运行 stop.ps1，再安装或启动 |
| 启动失败或 Worker 离线 | 查看 `data/logs/api.err.log`、`worker.err.log`，修复后先 stop 再 start |
| 端口不可用 | 通过 `-Port` 更换，不停止其他项目的程序 |
| 环境变量覆盖了目录 | 标准安装要求清除 `UV_PROJECT_ENVIRONMENT`；生命周期脚本要求清除 `VIDEO_DATA_DIR` |

后端开发检查在 `backend/` 执行 `.venv/Scripts/python.exe -m pytest -q` 和 `.venv/Scripts/python.exe -m ruff check app tests`；前端执行 `npm.cmd --prefix frontend run build`。大包与第三方弃用提示不等于失败，以命令退出状态为准。

## 6. 复现验证范围

验证使用仅含源码和锁文件的独立目录，不复制虚拟环境、前端构建、数据库或本机配置，重新安装并验证启动、自检、重启与停止。这能发现路径和产物依赖，但不能替代所有 Windows 版本、企业网络及 FFmpeg 发行包的实机验证。

请以目标机器的 setup.ps1 和 verify.ps1 成功退出作为环境验收依据。本机安装与开发验证记录单独归档，不随源码提交。
