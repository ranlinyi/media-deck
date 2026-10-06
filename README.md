# media-deck

原生单窗口「图片 + 视频 + Steam 录像」浏览器，专为 Steam Deck 游戏模式设计。
不使用浏览器、不启动外部播放器进程、不改动系统。

## 组成

  app.py                GTK4 原生界面（画廊 + 内嵌播放器 + 手柄 + 触控手势）
  media.py              扫描 / 缩略图 / Steam 录像转封装
  fetch-codec.sh        下载缺失的 libgstlibav.so 到 ./gst（H.264/AAC，不改系统）
  steamdeck-touchfix    保持 STEAM_TOUCH_CLICK_MODE=4，让游戏模式透传真触摸
                        （来自 https://github.com/ranlinyi/steamdeck-gamemode-multitouch-fix）
  launch.sh             启动器（自动套 touchfix）

## 安装

1. cp -r media-deck ~/.local/share/media-deck
2. chmod +x ~/.local/share/media-deck/launch.sh ~/.local/share/media-deck/fetch-codec.sh ~/.local/share/media-deck/steamdeck-touchfix
3. ~/.local/share/media-deck/fetch-codec.sh
4. Steam 添加非 Steam 游戏，目标指向 launch.sh，名称「媒体库」（可加 --fullscreen）

## 操作

网格（4 个一行，16:9 卡片，下方文件名 + 位置）：
- 左摇杆 / 十字键：移动选择
- A：打开
- L1 / R1（或 L2 / R2）：切换分类（全部 -> 图片 -> 视频 -> 录像）

查看器：
- A：播放 / 暂停
- B：返回
- 左 / 右：上一个 / 下一个
- 看视频时：L1 / R1（或 L2 / R2）快退 / 快进 5 秒
- 图片：双指捏合缩放；拖动平移；左右滑动切换

桌面 / 触屏：点击打开、滚轮滚动、捏合缩放。

## 说明

- 视频进度条只用 GtkVideo 自带的那一条（原来多出来的那条是我额外加的，已删）。
- 摇杆导航按“事件 + 冷却”驱动，松手即停，不会一直滑到底。
- 缓存：~/.cache/media-deck（缩略图 + 转封装 mp4，可随时删）
