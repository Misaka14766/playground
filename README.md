# 🧪 Playground

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

我的小玩意儿收藏 —— 一些有趣的、值得保存和分享的小项目。

每个子文件夹是一个独立的项目，通常是单页 HTML，直接用浏览器打开即可运行。

## 项目列表

| 项目 | 简介 | 技术 |
|------|------|------|
| [lif-neuron-detector](./lif-neuron-detector) | LIF神经元脉冲模式检测器，基于STDP学习规则的类脑计算教学演示 | HTML · Canvas · JavaScript |
| [einstein-chess](./einstein-chess) | 爱恩斯坦棋（EWN · Einstein würfelt nicht!）完整实现，支持人人/人机/AI自战/外部AI接入 | HTML · CSS · JavaScript · Python |

## einstein-chess

> 🎲 爱恩斯坦棋 —— 骰子决定命运，策略决定胜负

**游戏特性：**
- 🤝 人人对战（pvp）
- 🤖 人机对战（pva）— 内置入门 / 普通 / 困难三档 AI
- 👁 AI 自战（ava）— 观战模式
- 🔌 外部 AI 接入（ext）— 通过 WebSocket 接入自定义 AI

**文件说明：**
-  — 游戏主体，纯前端单文件，无需任何依赖，浏览器直接打开即可
-  — WebSocket 中继服务，桥接浏览器与外部 AI 进程
-  — 外部 AI 接入示例，含随机 / 贪心 / Minimax 三种策略

**快速上手外部 AI：**
```bash
pip install websockets
python ai_server.py        # 启动中继服务（localhost:8765）
python ai_example.py       # 另开终端，启动示例 AI
# 浏览器打开 index.html → 选「外部AI」模式
```

---

> 持续更新中。
