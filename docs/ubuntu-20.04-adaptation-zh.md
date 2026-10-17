# UU 远程：Ubuntu 20.04 适配过程与 22.04 迁移参考

整理日期：2026-10-17。本文记录本机实际修改、测试结果及其限制，供另一套 Ubuntu 22.04 系统适配时参考。

**本文是迁移参考，不代表已经完成 Ubuntu 22.04 适配。整理本文时没有安装软件、构建组件、重启服务或修改显示设置。**

## 1. 结论与适用范围

20.04 与 22.04 可以复用大部分 X11/VNC 桥接代码，但不能简单认为更换版本判断后就已适配。

已经验证的是：x86-64 Ubuntu 20.04.6、已登录的 GNOME/Xorg 桌面、Wine 11.0、UU 4.43.1.3614，以及本地 x11vnc/TigerVNC 桥接。手机已实际连接并操作过；曾出现鼠标问题、残影和卡顿，随后进行了针对性修复。仍不能保证控制端持续达到 60 帧，也没有实现超级屏或控制端比例自动满屏。

22.04 的建议起点是保留这套 X11/VNC 路线，先验证相同功能，再考虑其他后端。不要同时更换桌面协议、Wine、UU 版本和输入路线，否则故障难以定位。

用户要求必须保留：

- 不因控制端的尺寸或比例改变本机显示器分辨率、刷新率、系统缩放和窗口布局。
- 不默认创建独立远程桌面；当前用途是控制本机已经登录的同一个桌面。
- 不在桌面放 UU 快捷方式，避免应用列表里出现多个重复的 UU 入口。
- 关闭管理窗口后，后台远控可以继续运行；Ubuntu 顶栏需要可用的托盘入口。

### 源码现状

原项目：<https://github.com/llmir/uu-remote-ubuntu-plus>。

原始本机工作目录为 `$HOME/uu-remote-ubuntu-plus`，工作分支为 `compat/ubuntu-20.04-x11`。本仓库发布分支为 `ubuntu-20.04`，已收录本地适配；**重新克隆上游 llmir 的 main 不等于取得这些改动**。

另有 `$HOME/uu-remote-ubuntu22.04`，它目前只是准备阶段复制出的源码目录及 WineHQ 索引，不是完成适配或测试的 22.04 发行包。用户要求停止在当前系统继续适配，因此尚未新增可用的 22.04 专用安装入口。

## 2. 实际采用的架构

图像链路：

```text
已登录的 Ubuntu 实体桌面
    → 本机 x11vnc（仅监听回环地址）
    → 私有 Xvfb 桌面里的 TigerVNC Viewer
    → Wine 中的 UU 服务端采集、编码和传输
    → 手机或平板上的 UU
```

输入链路：

```text
控制端输入
    → UU 的用户态输入路径
    → uu-input-bridge-x11.dll
    → uu-input-broker.exe
    → 带认证的本地 uu-x11-input
    → Ubuntu 实体 X11 桌面
```

UU 的账号、设置管理窗口，通过单独的管理查看器显示到 Ubuntu。管理窗口与真正被控的桌面是两条不同的显示路径。

这里安装的是 **Windows 官方 UU 在 Wine 中运行**，不是网易发布的 Linux 原生 UU。Wine 本身不能让 Windows 内核输入、虚拟显示驱动直接变成 Linux 驱动；桥接层负责替代所需的桌面采集与输入能力。

本机实体显示曾为 `:1`，私有 UU 桌面曾为 `:20`；这些只是本机观测值。迁移后必须自动发现实际会话，不能在安装或运行脚本里固定复制这些显示编号、UID、进程号或 Xauthority 路径。

## 3. 20.04 上的安装和 Wine 处理过程

### 3.1 改用 X11/VNC 路线

新增 `scripts/install-ubuntu20-x11.sh`，要求 Ubuntu 20.04、x86-64、Xorg 会话，并选择：

```text
UURB_INPUT_ROUTE=legacy
UURB_DESKTOP_RELAY=vnc
UURB_KEYBOARD_ROUTE=x11
UURB_FOLLOW_DESKTOP_RESOLUTION=on
```

`FOLLOW_DESKTOP_RESOLUTION=on` 的含义是：实体桌面尺寸稳定改变后，桥接画布跟随该尺寸，必要时重连桥接。**它不是由控制端尺寸反过来修改本机屏幕。**

VNC 分支跳过未使用的 GNOME RDP 配置、FreeRDP 构建、RDP 凭据设置和 libei 回移植。这避免让 20.04 因缺少较新 GNOME 的命令或接口而安装失败。

依赖的当前清单在 `install.sh` 的 `install_packages()` 中。核心类别包括：

| 用途 | 组件 |
| --- | --- |
| Windows 应用运行 | Wine、匹配的 wineserver/winegcc；安装器可能需要 32 位运行支持 |
| 编译适配组件 | GCC、MinGW、binutils、构建工具 |
| 私有桌面与图像桥接 | Xvfb、Openbox、x11vnc、TigerVNC Viewer |
| 原生输入及剪贴板 | X11/XTEST、Python Xlib、Pillow、xclip、GTK/PyGObject |
| 管理界面 | xdotool、XComposite/XRender、GTK 及其他脚本依赖 |
| 顶栏入口 | AppIndicator 或 Ayatana AppIndicator 运行库、支持该协议的桌面托盘 |

不要把此表当作完整的可复制 APT 安装命令。22.04 应按目标系统的软件包索引核对实际包名和依赖。

### 3.2 Wine 10 与 Wine 11 分工

本机 WineHQ 10.0 及相应 i386 依赖能够完成安装器引导，但 UU GUI 曾停留在启动画面。一个 Wine 10.6 的私有试验也没有解决该启动问题。

之后在用户目录编译 Wine 11.0，完整 UU 管理窗口能够显示，最终运行环境选为：

```text
$HOME/.local/opt/uu-wine11/bin/wine
```

这份私有 Wine 11 是 64 位构建，**不是完整的 32/64 位安装环境**。本机实际使用系统 Wine 10 完成引导，再使用私有 Wine 11 运行。不能把该 64 位 Wine 路径直接用于一台空白机器，并假定 32 位安装器也一定能运行。

历史上的 Wine 源码校验值：

```text
来源：https://dl.winehq.org/wine/source/11.0/wine-11.0.tar.xz
SHA256：c07a6857933c1fc60dff5448d79f39c92481c1e9db5aa628db9d0358446e0701
```

历史构建采用 `--enable-win64 --with-mingw --without-wayland --without-vulkan`，并私下准备了 GnuTLS 开发头文件和库链接。这是本机恢复 GUI 的实测路径，不是要求 22.04 必须采用相同编译选项。

22.04 应优先评估原生 Jammy WineHQ 11 的完整软件包。2026-10-17 读取官方 Jammy 索引时，以下包存在相同版本 `11.0.0.0~jammy-1`：

```text
winehq-stable
wine-stable
wine-stable-amd64
wine-stable-dev
```

该索引中的 `wine-stable` 依赖匹配版本的 amd64 和 i386 组件；`wine-stable-dev` 也要求匹配的运行组件。**这只验证了软件包存在，没有在用户的 22.04 系统上安装或测试。**

官方源文件：<https://dl.winehq.org/wine-builds/ubuntu/dists/jammy/winehq-jammy.sources>。

本机脚本已让桥接、管理窗口、CLI、注册表修复及检查器读取保存的 `UURB_WINE_BIN`，并使用同目录下匹配的 Wine 工具。迁移时应保留这种选择机制，不要某些脚本用 Wine 11，另一些却固定调用 `/opt/wine-stable/bin/wine` 或错误版本的 wineserver。

### 3.3 固定 UU 版本，保留二进制校验

原先的 UU 4.42 下载地址后来返回 403。官方滚动下载接口当时提供的是 4.43.1.3614，因此新增了本地审核清单：

```text
patches/uu-remote-4.43.1.3614.json
```

关键校验值：

| 文件/状态 | SHA256 |
| --- | --- |
| `uu-4.43.1.3614.exe` 安装器 | `9a69473798ad4861b966607859adbba9c8d1b2a2a0ea9e22bba7f14969656263` |
| 原版 `GameViewerServer.exe` | `ecc3b0b0b5fefacb0f77c2f00d494647127887760d3eb8d94d07ee1b7dbdd2c6` |
| 补丁后 `GameViewerServer.exe` | `a3b4d64715032b51a7bae1f127292e76542d4b7879abbabb255d8f4f9228a09e` |

两个输入补丁分别位于此版本文件偏移 `0x5b1b52` 和 `0x5b1c04`，用于阻止设置读取和运行时刷新重新启用不适用的虚拟输入路径。

这些偏移和哈希仅适用于被审核的文件；它们不是可跨版本照抄的通用补丁。下载接口滚动返回新版本时，应停止、重新审查，不能关闭校验继续安装。

同时保留了健康监控替代程序、被抑制的 Windows 驱动安装工具原文件备份、注册表清理和终端运行时身份校验。Wine 蓝牙模块另由专用前缀进程的 `WINEDLLOVERRIDES` 抑制，仅设置服务注册表 `Start=4` 在本机不够。

### 3.4 历史安装阶段示例

以下是 **20.04 历史流程**，不是 22.04 安装命令，也没有在整理本文时执行：

```bash
cd "$HOME/uu-remote-ubuntu-plus"
bash scripts/install-ubuntu20-x11.sh --packages-only
```

准备依赖后，使用能够运行安装器的系统 Wine 引导专用前缀：

```bash
UURB_WINE_BIN=/opt/wine-stable/bin/wine \
  bash scripts/install-ubuntu20-x11.sh \
    --skip-packages --skip-account-login --no-start \
    --uu-installer "$HOME/uu-remote-ubuntu-plus/build/downloads/uu-4.43.1.3614.exe"
```

在 Wine 11 已另行准备完成、UU 文件已经安装的前提下，再使用选定运行时部署：

```bash
UURB_WINE_BIN="$HOME/.local/opt/uu-wine11/bin/wine" \
  bash scripts/install-ubuntu20-x11.sh \
    --skip-packages --skip-account-login
```

重新安装或切换运行时可能中断远控。22.04 不应直接运行上述专门限制为 20.04 的入口。

## 4. 旧系统兼容层改动

这些修复比“修改系统版本判断”更重要。22.04 的环境较新，但不应因此直接删除已经解决实际问题的兼容逻辑。

| 问题 | 20.04 中的处理 | 迁移建议 |
| --- | --- | --- |
| Python 3.8 不支持部分较新字符串 API | 改用兼容写法 | 保留；对新 Python 也适用 |
| 旧 Python Xlib 缺少所需 XFixes 事件定义 | 在项目内补充协议结构 | 核对系统版本，避免重复注册，不修改系统 Xlib |
| glibc 2.31 缺少所用的较新关描述符接口 | 提供子进程中的兼容实现 | 保留可编译的后备路径 |
| Bash 5.0 的 `wait -n` 缺少较新参数 | 调整进程退出监测 | 用目标 Bash 实际验证，不只做语法检查 |
| MinGW 格式检查及 Windows API 声明差异 | 显式格式宏和 API 版本宏 | 在目标 MinGW 下重建并检查警告 |
| Focal 的 cgroup 层次不同 | 兼容 systemd/unified 与 cgroup v2 | 保留多布局发现逻辑 |
| TigerVNC 1.10 参数与其他查看器不同 | 按查看器能力选择参数 | 在 Jammy 中查询实际帮助和版本 |
| 新安装目录不存在 | 提前创建运行工具目录 | 验证空白 HOME 的安装路径 |

## 5. 输入、鼠标和管理窗口修复过程

### 5.1 鼠标移到管理窗口时弹回屏幕中央

管理窗口覆盖私有 VNC 查看器时，TigerVNC/FLTK 会处理离开事件并向底层发送旧指针位置，造成对实体鼠标的反馈。

处理包括：

- TigerVNC 使用正常的无边框最大化窗口，避免全屏窗口始终压过管理窗口。
- 管理输入注入前请求正确的窗口堆叠，并确认鼠标实际命中管理窗口。
- 尽量保留原有键盘焦点。
- 为私有 VNC 查看器单独加载 `uu-vnc-relay-guard.so`，过滤造成反馈的跨窗事件。

对应代码主要在 `src/uu_manager_capture.c`、`src/uu_vnc_relay_guard.c`、`scripts/uu-remote-console`、`scripts/uu-remote-bridge`。

回归测试使用两个独立 Xvfb、真实 Openbox/TigerVNC/x11vnc，检查管理悬停不会移动实体测试桌面的指针，真正的桌面输入仍能传递。

### 5.2 远端可以操作，但看不到光标

实体鼠标与 Wine 私有桌面光标不属于同一个桌面，不能依赖 Wine 的光标来表示真实位置。

本地 x11vnc 使用：

```text
-cursor most -nocursorshape -nocursorpos
```

让实体桌面的光标绘制到转发画面中；输入仍通过原生 X11 helper 注入。迁移验收需分别检查“能点到”和“能看到光标”，不能只通过输入日志判断成功。

### 5.3 关闭管理窗口后，UU 提示一直盖在远程桌面上

修复了私有查看器窗口查找和堆叠恢复：管理窗口关闭后，恢复桌面 relay 的层级；管理窗口正在打开时，监督器不应与它反复争抢层级。

另外发现“更多”悬停提示会成为独立的 Wine/Qt 小浮窗。仅抬高桌面窗口覆盖不了 override-redirect 提示，所以 `dismiss_manager_tooltips()` 在关闭管理查看器时清理已核实属于该 GUI 进程的小提示框。

清理同时核对窗口所属进程、类名、标题、尺寸、深度、输入提示和窗口属性；没有杀死 UU 进程，也没有泛化成关闭所有同名窗口。

相关回归测试是 `tests/test_console_focus.py`、`tests/test_manager_vnc_hover.py`、`tests/test_manager_tooltip_cleanup.py`。

## 6. 卡顿与残影：最终保留的采集参数

问题主要在开关页面、拖动和大范围重绘时明显。先看录屏，再用局部测试标记测量实体桌面至私有画布的链路，而不是只看静止桌面的 CPU 或标称刷新率。

最终 `scripts/uu-remote-bridge` 中的参数为：

```text
-wait 1 -defer 0 -setdefer 0 -nonap -sb 0
-nowireframe -nowirecopyrect -noscrollcopyrect
-noxdamage -snapfb
```

TigerVNC 路线的采集缩放参数为：

```bash
-scale "${resolution}:nocr"
```

各项处理的原因：

| 改动 | 解决的问题 |
| --- | --- |
| `-nowireframe` | 禁止猜测窗口拖动时暂停或延后实际画面更新 |
| 禁止窗口/滚动 CopyRect，缩放使用 `nocr` | 避免移动/滚动区域复制留下重复条带与错误区域 |
| `-noxdamage -snapfb` | 先取得完整快照再扫描，减少大重绘时混入不同时刻画面的现象 |
| `-nonap -sb 0` | 原生 X11 输入不经过 VNC，不能依赖 VNC 输入事件退出闲置节流 |
| `wait` 从 5ms 调到 1ms | 在本机对比中降低采集等待与更新间隔抖动 |

### 实测证据及边界

实体 GNOME → x11vnc → 私有 TigerVNC/Xvfb 的大范围重绘测试中：

- DAMAGE 开启时，观测到约 47.5 次本地画面更新/秒。
- 关闭 DAMAGE 时约 51.3 次/秒。
- 关闭 DAMAGE 并启用快照时约 59.4 次/秒。
- 两轮 5ms/1ms 对比中，本地绘制至转发延迟中位数约从 26ms 降到 19.4–20.0ms，更新率保持约 59–60 次/秒。

**这些是本地链路测试，不是手机实际收到的帧率，也不是端到端延迟。** `-snapfb` 和短轮询有额外内存访问/CPU 成本，22.04 应使用同样场景复测，不应无条件视为所有机器的最优参数。

手机连接时的另一次采样中，编码线程有明显 CPU 活动，NVIDIA 编码利用率为 0，主机网络队列未见明显积压。但这不能独立证明软件编码就是唯一瓶颈，也不能排除网络途中丢包、时延抖动和手机解码/呈现问题。

`uu-agent cli device status --json` 在编码线程工作时仍曾报告没有活动连接。因此不能仅凭该字段断言手机未连接，也不能把静止、未编码时的 GPU 数据用作串流性能证据。

原始本机证据位于未随源码上传的 `build/`，以下为本机历史文件名，并非仓库附件：

```text
snapshot-capture-report.json
real-desktop-relay-probe-large.json
remote2-wait-comparison.json
remote2-optimization-report.json
```

## 7. 关闭窗口后的后台运行与 Ubuntu 托盘

关闭管理窗口不等于停止后台服务。原 Windows 托盘位于私有 Wine/X11 桌面，Ubuntu 顶栏看不到它，所以另加了原生宿主 AppIndicator。

源码文件：

```text
scripts/uu-remote-tray.py
systemd/uu-remote-tray.service
desktop/uu-remote-tray.desktop
```

安装位置：

```text
~/.local/libexec/uu-remote-tray.py
~/.config/systemd/user/uu-remote-tray.service
~/.config/autostart/uu-remote-tray.desktop
```

托盘提供后台状态、打开管理窗口、启动或停止后台远控的入口。停止后台远控会断开控制端连接；关闭管理窗口则不会。

实现通过 GTK/AppIndicator 的公开 C 接口和 Python ctypes 使用现有运行库，不要求 Focal 上额外存在 AppIndicator 的 GI typelib。还支持查找 Ayatana AppIndicator 运行库。

20.04 本机已验证托盘注册成功、菜单状态正确，启动托盘没有改变当前焦点，也没有重启 UU。22.04 需检查 GNOME 托盘扩展、用户会话环境、登录自启动和库是否齐全。

安装、卸载与源码摘要已纳入托盘文件。迁移时不要只复制图标脚本，遗漏服务、自启动和卸载清理。

## 8. 分辨率、满屏和超级屏：不要误诊

本机原始显示为 1920×1080、约 240Hz；平板为 2800×1840，即 35:23。两者比例不同，完整且等比例显示时出现留黑是几何结果，不能单凭留黑判断 UU 图像桥接失败。

用户在控制端选择不同分辨率时，UU 提示改用超级屏，随后报“超级屏创建失败，错误码 501”。目前没有确认错误码 501 的官方精确定义；当前方案没有实现该 Windows 虚拟显示能力，不能承诺可用。

“缩放 100%”入口也曾提示被控端不支持、要求 Windows 10 以上。但本机 UU 的诊断已经报告 `Windows 10 64-bit`。这个入口涉及被控端系统显示能力，不能直接当作平板端画面铺满，也不能认为只修改 Wine 的版本号即可解决。

曾用 NVIDIA ViewPort 设置让本机桌面和桥接都变为 1400×920。它虽与平板同比例，但实际影响了本机显示，违背用户要求，因此**已经完整撤销**；本机和 UU 画布恢复 1920×1080，原刷新率恢复，临时改屏工具移除，相关回退定时器已取消。

后续只在临时独立 Xvfb 中验证过 1400×920、1920×1080、2800×1840 模式切换。这不等于已经实现运行中的 VNC 画布动态切换、UU 超级屏或平板自动满屏。

迁移 22.04 时必须遵守：

- 不把 `xrandr`、`nvidia-settings` 或本机 GNOME 缩放修改当成默认“适配控制端”的办法。
- 私有画布尺寸变化与实体桌面变化分开处理，并同步验证指针映射。
- 原始桌面仍为 16:9 时，单纯改变平板显示只能在留黑、裁切、拉伸之间取舍。
- 另建桌面/虚拟显示区是不同的产品行为，需要明确说明窗口是否同步；本用户没有选择独立远程桌面。

## 9. 20.04 → 22.04：可复用与需要重新验证的部分

22.04 使用更新的用户空间；官方发行说明列出 GCC 11.2、glibc 2.35、Python 3.10、GNOME 41/42 的更新，并说明许多系统默认使用 Wayland。目标系统经过更新后具体包版本仍应现场检查。[Ubuntu 官方发行说明](https://discourse.ubuntu.com/t/jammy-jellyfish-release-notes/24668)

| 项目 | 可复用内容 | 22.04 要处理的差异 |
| --- | --- | --- |
| 桌面采集 | 当前 X11/VNC 管线 | 登录“Ubuntu on Xorg”；不能把 Wayland 会话当成已支持 |
| 原生输入 | broker、认证 X11 helper、已审核的 UU 输入补丁 | 检查 Xauthority、真实会话、权限和键鼠映射 |
| 软件源 | 官方 WineHQ 配置方式 | 使用 `jammy`，不能带入 Focal 的源或依赖包 |
| Wine | 选定运行时及匹配工具的机制 | 优先评估 Jammy Wine 11 的完整包，GUI 与安装器都需验证 |
| 构建 | C/MinGW 代码及旧平台兼容修复 | 用目标系统编译器和 Wine 工具重建 |
| systemd/cgroup | 已有多布局发现与用户服务 | 检查 22.04 的实际层次、会话环境和服务重启行为 |
| 管理窗口 | 焦点、堆叠、鼠标反馈与提示框修复 | 重测 Jammy 的 TigerVNC、Openbox 和 XComposite 行为 |
| 流畅度 | 禁用启发式复制、快照和低等待设置 | 同场景复测 CPU 成本、帧间隔及真实控制端体验 |
| 托盘 | 宿主 GTK/AppIndicator 图标 | 核对运行库、GNOME 扩展和登录启动 |
| 满屏/超级屏 | 已知限制与故障排查经验 | 不能把迁移系统等同于解决这些功能 |

### 9.1 先做只读预检

下面的命令供 **进入目标 22.04 系统后**参考。本文整理期间未执行它们：

```bash
cat /etc/os-release
uname -m
printf '%s\n' "$XDG_SESSION_TYPE"
printf '%s\n' "$DISPLAY"
printf '%s\n' "$XAUTHORITY"
```

应确认 Ubuntu 22.04、x86_64，并在真正登录的 Xorg 桌面终端中进行安装。不要在当前 20.04 上安装 Jammy 包来模拟升级。

### 9.2 需要补齐的安装入口

现有 `scripts/install-ubuntu20-x11.sh` 只接受 20.04。通用 `install.sh` 的默认验证名单是 24.04/26.04；`UURB_ALLOW_UNVALIDATED_UBUNTU=1` 只是允许尝试，不代表通过验证。

正式制作 22.04 版本时应：

1. 单独新增严格检查 Ubuntu 22.04、x86_64、Xorg 的入口；保留 20.04 入口。
2. 明确选择 `vnc`、`legacy`、`x11`，复用已验证路线。
3. 保留实体桌面跟随语义，禁止由控制端请求改变本机显示设置。
4. 选择已审核的 UU 4.43.1.3614 manifest，并使用哈希匹配的安装器。
5. 在停止旧服务或修改前缀之前，检查 Wine 版本、winegcc/wineserver 配套、构建工具、Python/GTK 运行库和目标会话。
6. 若沿用旧 Wine，要显式处理升级或拒绝继续；不能只因 `/opt/wine-stable/bin/wine` 存在就假定版本合适。
7. 从官方 Jammy 源安装目标依赖，不能把当前系统的 Focal `.deb` 当作 22.04 依赖包复制安装。
8. 对齐安装、验证、升级和卸载中的版本识别、哈希与备份规则。
9. 先做不启动服务的部署检查，再进行实际登录与远控测试。

原适配记录曾发现 `uninstall.sh` 的 devcon 版本分支漏列 4.43.1.3614。发布整理时已将它补入与安装/验证一致的原文件哈希分支，保留全部校验。此修正未在当前运行环境执行卸载；22.04 交付前仍需实际验收卸载流程。

### 9.3 桌面快捷方式与重复入口

当前 20.04 上的桌面 UU 快捷方式已按用户要求移除，应用列表保留主要 UU 入口，画质入口可从其动作菜单打开。

通用安装器仍含创建 `$HOME/Desktop/UU Remote.desktop` 的逻辑，以及额外工具入口生成逻辑。**当前桌面已经清理，不代表新系统重装会自动保持同样外观。** 22.04 入口应加入明确的“不创建桌面快捷方式”策略，并检查本地语言桌面目录与 Wine 自动生成入口。

不要为隐藏 UU 依赖工具而删除用户原有的其他远控软件；只处理确认属于本次安装的启动器。

## 10. 迁移验收与检查命令

以下命令仅列为目标系统上的验证步骤，本文整理期间没有运行测试或操作服务。

### 文件和组件检查

在目标源码目录中：

```bash
bash scripts/verify.sh --quick
```

该检查会核对运行服务、选定的 UU 版本、补丁和备份、源码摘要、输入组件、私有画布/源桌面尺寸及相关运行配置。它不能代替控制端实测。

可参考已有回归测试；其中的 `ubuntu20` 名称表示历史来源，不等于只能在 20.04 上验证：

```bash
env PYTHONPATH=tests /usr/bin/python3 -m unittest \
  test_ubuntu20_x11_install \
  test_console_focus \
  test_manager_vnc_hover \
  test_manager_tooltip_cleanup \
  test_terminal_runtime
```

这些涉及 X11 的回归测试会使用临时显示服务器；需要先准备其工具依赖。不要把它们改成在用户真实桌面上注入测试输入。

### 实际远控验收

| 场景 | 应确认的结果 |
| --- | --- |
| GUI 启动与登录 | 完整窗口显示，账号可登录，控制端能连接 |
| 鼠标 | 可见、点击位置正确、拖动和滚轮正确、不跳屏幕中心 |
| 键盘与文字 | Shift/Ctrl 等组合键正常，中文、多行文本和剪贴板可用 |
| 管理窗口 | 悬停不反馈到实体鼠标；关闭后不覆盖桌面、不残留“更多” |
| 页面开关与窗口拖动 | 无明显重复条带；记录帧间隔和体感，不只看标称 60 帧 |
| 后台与托盘 | 关闭管理窗口后远控继续；顶栏可再次打开窗口和管理服务 |
| 显示设置 | 本机原分辨率、刷新率、缩放和窗口布局保持原样 |
| 重连/登录启动 | 退出或重新登录后的服务、托盘行为符合预期 |
| 卸载 | 已确认的 UU 文件与服务可清理，其他软件和用户文件保留 |

如果有登录后的录屏、测试数据或日志，避免将账号状态、验证码、连接地址和完整个人桌面内容打入迁移源码包。

## 11. 部署与回退经验

20.04 的修改采用源文件、已安装运行文件和 `.runtime-source-sha256` 一致的方式部署。启动脚本/组件有更改时，先备份，再重建或安装对应文件，并执行相关检查。

不要只更新摘要文件来掩盖尚未部署的运行代码。新增独立托盘时只需启动托盘，不需要重启 UU；涉及桥接启动、Wine 或画布的变化则可能需要重连。

在其他系统迁移时优先传递修改后的源码、审核清单和已校验安装器。不应默认复制整个个人 Wine 前缀；它可能含账号、设备身份与会话信息，也不能代替目标系统上的运行验证。

不要把旧记录中的 PID、`:1`、`:20`、用户名目录或某一次备份路径当成通用脚本常量。

## 12. 给 22.04 适配者的交接摘要

先复用 Xorg + x11vnc + TigerVNC + 原生 X11 输入路线；优先核实 Jammy Wine 11 及匹配开发包。输入、管理窗口、残影和托盘修复都有现成代码可参考。版本入口、依赖和 Wine 选择需要重新整理，安装/验证/卸载规则需要一致。

22.04 的安装、完整桌面运行和手机/平板实测尚未完成。当前没有可保证直接运行的 22.04 安装包，也没有解决“本机完全不变而控制端自动无黑边满屏”。这些限制必须保留在后续交付说明中。

## 13. 本次源码发布检查

2026-10-17，在独立源码副本中运行第 10 节列出的五个测试模块，45 项测试全部通过；另检查了本次修改的 13 个 Bash 脚本、9 个 Python 文件的语法，以及 UU manifest 和 `git diff --check`。测试中的 X11 桌面与当前实体桌面分离，没有重新部署或重启现有 UU。

本次仅将原始未提交适配整理为源码版本，并补齐卸载器的 4.43.1 识别。未运行实际安装/卸载，没有重新测量控制端帧率，也没有在 22.04 上测试。
