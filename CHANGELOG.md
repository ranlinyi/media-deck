# Changelog

## 2026-10-07
- 首次发布
- 原生 GTK4 单窗口浏览器：图片 / 视频 / Steam 录像
- 统一画廊：一行 4 个 16:9 卡片；文件夹视图（收纳成带预览的文件夹卡片）
- 图片与视频共用同一套缩放/拖动（自定义 Gdk.Paintable 在快照阶段缩放，控件尺寸不变）
- Steam 录像 DASH 分片自动转封装（ffmpeg）
- 视频自动循环
- 随 app 附带 gst-libav（H.264/AAC），不改系统
- 内置 evdev 手柄支持（左摇杆轮询、十字键、A/B、L1/R1 切分类或快进快退）
- 集成 steamdeck-touchfix，让游戏模式透传真触摸
- Steam 四卡槽自定义美术（hero 配图改为图标在左、标题在右的镜像排布）
- 一键安装脚本 install.sh
- artwork/make-preview.sh：一键复现 README 顶部的配图总览
