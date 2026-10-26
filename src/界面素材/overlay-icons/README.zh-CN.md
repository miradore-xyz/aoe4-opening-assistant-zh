# 悬浮窗金色锁图标

`lock-closed-gold.svg`（闭锁）和 `lock-open-gold.svg`（开锁）派生自 Microsoft Fluent UI System Icons（微软 Fluent 系统图标）的 24 像素实心图形，固定提交：`82ecf7cbdb43684403c7125efa160be04a33dca5`。

- 闭锁原图：https://github.com/microsoft/fluentui-system-icons/blob/82ecf7cbdb43684403c7125efa160be04a33dca5/assets/Lock%20Closed/SVG/ic_fluent_lock_closed_24_filled.svg
- 开锁原图：https://github.com/microsoft/fluentui-system-icons/blob/82ecf7cbdb43684403c7125efa160be04a33dca5/assets/Lock%20Open/SVG/ic_fluent_lock_open_24_filled.svg
- MIT 许可：https://github.com/microsoft/fluentui-system-icons/blob/82ecf7cbdb43684403c7125efa160be04a33dca5/LICENSE

原图版权：Copyright (c) 2020 Microsoft Corporation（微软公司版权所有）。本项目只调整金色渐变、细暗边和轻微底缘暗影；并非微软原版配色。完整许可和版权声明保存在同目录 `LICENSE.MIT.txt`，须与这两个派生图标一同分发。

生产界面用 `QSvgRenderer`（Qt 矢量绘制器）将图形绘制为 18 逻辑像素，置于 28×28 逻辑像素点击区。普通状态没有独立底色或边框，悬停才显示轻微蓝底；近乎不可见的非零透明度命中层用于避免 Windows 将小锁中心的点击交给游戏。图形表达当前开锁／闭锁状态，点击动作由中文悬停说明与无障碍名称表达。

源码路径为 `界面素材/overlay-icons`，冻结程序路径为 `assets/design/overlay-icons`。本目录仅含生产使用的矢量资源和许可，不包含审核截图、预览图或玩家资料。
