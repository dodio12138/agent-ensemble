# Agent Ensemble

在本机运行的多智能体虚拟乐队。鼓、贝斯、钢琴（或六轨现代乐队）各由一个独立的乐手 Agent 演奏，统一的调度器负责节拍、小节锁定和 MIDI 输出。Agent 决定演奏什么，调度器决定什么时候演奏。

![CI](https://github.com/dodio12138/agent-ensemble/actions/workflows/ci.yml/badge.svg)

## 功能

- **乐手协作**：鼓、贝斯、钢琴提前两小节规划，可在独立、共享状态和显式协商三种模式间切换；某个乐手超时或出错时，该小节由规则兜底，演奏不中断。
- **自然语言指挥**：本机 Qwen2.5-1.5B 理解风格、速度、拍号和编配指令，也能即兴变奏和编排整首器乐曲；模型不可用时退回本地规则。
- **MIDI**：导出多轨 MIDI，播放本地 MIDI 文件，接入 USB MIDI 键盘让乐队跟随弹奏。
- **音源**：浏览器 Web Audio（默认）、FluidSynth + GeneralUser GS，或 SuperCollider，断开时自动切换备用音源。
- **同步与分布式**：Ableton Link 共享速度、小节相位和启停；外部进程可通过 WebSocket 接管任意一轨。
- **低抖动调度**：原生 C 计时器，音符唤醒延迟 P99 约 0.7 ms。

## 快速开始

支持 macOS 和 glibc Linux（Intel / ARM），需要 Bash、curl、tar 和 shasum。首次运行会把工具链和依赖下载到项目目录内，之后可离线使用。

```sh
./scripts/run --open
```

浏览器打开 http://127.0.0.1:8765 后点「播放」。macOS 上也可以双击 `启动乐队.command`。

可选组件：

```sh
./scripts/setup-llm            # 本地指挥模型（约 1.1 GB）
./scripts/setup-native         # FluidSynth 与 GeneralUser GS 音色
./scripts/setup-supercollider  # SuperCollider 音色（macOS）
```

启动参数：`--native` 使用 FluidSynth，`--supercollider` 使用 SuperCollider，`--rules-only` 不加载模型，`--port` 和 `--state-dir` 可并行运行多个实例。

## 文档

- [使用指南](docs/guide.md)：控制台、指挥指令、即兴写法、MIDI 键盘、音源、Link、远程乐手和 API 示例
- [开发设计](DEVELOPMENT.md)：架构、时间系统、Agent 设计与里程碑

## 开发

```sh
.venv/bin/ruff check band scripts tests
.venv/bin/pytest
.venv/bin/python -m scripts.verify_soak --seconds 600   # 十分钟连续演奏测试
```

后端是 Python 3.12 + FastAPI（`band/`），控制台是 React + Vite（`frontend/`）。

## 许可

本项目以 [GPL-3.0](LICENSE) 发布。GeneralUser GS 音色、Qwen2.5 模型权重、SuperCollider 和 Ableton Link（经 [aalink](https://github.com/artfwo/aalink)）各自遵循其许可，由安装脚本下载，不包含在本仓库中。
