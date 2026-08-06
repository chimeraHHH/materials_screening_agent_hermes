# Windows 10 最简部署指南

目标：远程操作一台全新的 Windows 10 电脑，把本项目跑起来。

这条路径只需要安装 **Python 3.11 64 位**。不需要安装 Git、Conda、VS Code，
也不需要手动激活虚拟环境。

## 一、先把项目复制到 Windows

在原电脑上把项目目录压缩成 zip，复制到 Windows 电脑。

不要复制这些目录或文件：

- `.venv`
- `.venv-agent02`
- `.venv-alignn`
- `workspace`
- `__pycache__`

在 Windows 上解压到一个短路径，例如：

```text
C:\material_screening_agent
```

解压后，目录中应能看到：

```text
C:\material_screening_agent\pyproject.toml
C:\material_screening_agent\requirements.lock
C:\material_screening_agent\src
```

## 二、安装 Python

在 Windows 浏览器打开 [Python 官方 Windows 下载页](https://www.python.org/downloads/windows/)，
下载并安装 **Python 3.11.x Windows installer (64-bit)**。

安装界面第一步务必勾选：

```text
Add python.exe to PATH
```

其余保持默认，点击 **Install Now**。

安装完成后，关闭并重新打开 PowerShell，执行：

```powershell
py -3.11 --version
```

看到 `Python 3.11.x` 就可以继续。不要使用 Python 3.12 或 3.13。

## 三、安装项目依赖

点击“开始”菜单，搜索并打开系统自带的 **Windows PowerShell**，然后把下面命令逐行复制执行：

```powershell
cd C:\material_screening_agent
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
```

安装过程需要联网，可能需要几分钟。

如果某一行出现红色错误，先停止，不要继续执行后面的命令，并保留错误信息。

## 四、确认部署成功

继续执行：

```powershell
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\material-agent.exe --help
```

如果第一条显示 `No broken requirements found`，第二条显示帮助信息，就表示部署成功。

如果运行项目时出现：

```text
ModuleNotFoundError: No module named 'fcntl'
```

说明使用的是旧压缩包。请重新获取包含最新源码的压缩包，并用其中的
`src\material_agent\orchestrator\runtime.py` 替换 Windows 项目中的同名文件；
`.venv` 和 `workspace` 不需要删除。

以后运行本项目时，都在项目目录使用下面这个程序：

```powershell
.\.venv\Scripts\material-agent.exe
```

不需要执行 `Activate.ps1`。

## 五、运行一次离线演示

确认部署成功后，可以执行：

```powershell
.\.venv\Scripts\material-agent.exe project create --workspace workspace --project-id demo
```

再执行：

```powershell
.\.venv\Scripts\material-agent.exe run `
  --workspace workspace `
  --project demo `
  --run-id run-demo `
  --source materials_project `
  --request "从 Materials Project 中寻找同时包含 Si 和 O、带隙为 0.5–1.0 eV、energy above hull 不超过 0.05 eV/atom 的非金属材料。" `
  --fixture tests\fixtures\mp-summary.si-o.json
```

该演示使用本地 fixture，不需要 API key。命令停在需求确认处是正常现象，不是报错。

## 六、真实查询的额外条件

只有连接 Materials Project 做真实查询时，才需要在当前 PowerShell 窗口设置：

```powershell
$env:MP_API_KEY = "你的 Materials Project API key"
```

不要把 API key 写入代码、配置文件或提交到 Git。

## 说明

- 主环境使用仓库根目录的 `requirements.lock`；
- Agent02/ALIGNN 等真实 ML 环境不属于本次最简部署，不要安装到 `.venv`；
- 当前依赖锁文件来自 macOS arm64 基线，Windows 10 尚未通过仓库 release Gate。若安装时
  出现某个固定依赖没有 Windows 可用版本，请保留完整错误信息，不要自行改版本。
