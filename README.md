# UU Remote Ubuntu 20.04 适配版

基于 [llmir/uu-remote-ubuntu-plus](https://github.com/llmir/uu-remote-ubuntu-plus) 的 Ubuntu 20.04 实验性适配，保留上游历史及 MIT 许可。原始桥接项目来自 [Lachlan Chen](https://github.com/lachlanchen/uu-remote-ubuntu-bridge)。上游说明另存于 [README.upstream.md](README.upstream.md)；其中较新 Ubuntu 的支持描述不代表本分支的实测范围。

本分支已在 **x86-64 Ubuntu 20.04.6、GNOME/Xorg、Wine 11.0、UU 4.43.1.3614** 上运行，手机曾实际连接并操作桌面。**Ubuntu 22.04 尚未安装验证；Wayland 不适用。**

这是通过 Wine 运行 Windows 官方 UU 的桥接适配，不是网易发布的 Linux 原生客户端。本仓库提供源码和适配记录，不包含 UU 安装器、私有 Wine 预编译包、账号前缀或个人配置。

## 工作方式

```text
已登录的 Ubuntu 桌面 → 本机 x11vnc → 私有 Xvfb 中的 TigerVNC
                    → Wine 中的 UU 服务端 → 手机 / 平板控制端
```

远程输入经桥接层和带认证的本地 X11 输入程序传回实体桌面。UU 管理窗口使用独立查看器，Ubuntu 顶栏托盘提供后台入口。

20.04 专用入口默认跟随实体桌面尺寸，不根据手机或平板的比例修改实体显示器。

## 本次适配

- 增加 20.04/Xorg 安装入口，使用 X11/VNC 和 legacy 输入路线，跳过未使用的 GNOME RDP、FreeRDP 和 libei 部署。
- 兼容 glibc 2.31、Python 3.8、Bash 5.0、旧版 Python Xlib 与 TigerVNC；统一读取选定的 Wine 路径及匹配工具。
- 增加 UU 4.43.1.3614 的版本清单和二进制校验，继续核对安装器、补丁和替代组件的身份。
- 修复管理窗口悬停引起的鼠标回弹、远程光标可见性，以及关闭管理窗口后的显示问题。
- 调整 VNC 的快照采集、CopyRect 与等待参数，减少窗口开关时的残影和本地采集停顿；清理滞留提示框。
- 增加原生 Ubuntu 托盘入口，方便重新打开管理窗口或控制后台服务。

具体修改、历史测量和限制见 [20.04 适配过程与 22.04 迁移参考](docs/ubuntu-20.04-adaptation-zh.md)。

## 安装前阅读

这份发布包含已经使用的源码，**尚不是在空白系统上一条命令完成的安装包**。请先阅读上述适配记录的第 3 节，尤其是 Wine 引导与运行阶段的区别。

1. 使用普通桌面用户登录 Ubuntu on Xorg。
2. 在仓库目录准备依赖：

   ```bash
   bash scripts/install-ubuntu20-x11.sh --packages-only
   ```

   此命令会通过 APT 和 WineHQ 的 Focal 源安装系统软件包，需要管理员权限。

3. 按适配记录准备经校验的 UU 安装器、专用 Wine 前缀和 Wine 运行环境。本机使用带 32/64 位支持的系统 Wine 10 完成安装器引导，再切换到私有 Wine 11 运行；Wine 10 单独运行 GUI 曾卡在启动界面。本机私有 Wine 11 只有 64 位，不能假定它也能在空白系统完成全部安装阶段。
4. **仅在 UU 文件已安装、对应 Wine 11 已准备完成后**，参考以下路径部署运行环境：

   ```bash
   UURB_WINE_BIN="$HOME/.local/opt/uu-wine11/bin/wine" \
     bash scripts/install-ubuntu20-x11.sh \
       --skip-packages --skip-account-login
   ```

   示例中的 Wine 路径需要自行准备。重新部署可能中断已有远控。

UU 官方下载接口会滚动更新。如果返回文件与清单哈希不符，应停止并重新审核版本，不能绕过校验。本次发布没有重新下载或启动安装器。

安装器仍保留部分上游快捷方式生成逻辑；当前机器曾做过的桌面图标清理不等于所有新安装都会自动完成相同清理。

## 日常使用

```bash
uu-remote open
uu-remote status
bash scripts/verify.sh --quick
```

首次打开管理窗口后登录 UU，再由控制端连接。关闭管理窗口可让后台远控继续运行，可从 Ubuntu 托盘重新打开。

## 已知限制

- 本地采集测试接近每秒 60 次更新，不代表手机端持续 60 FPS；编码、网络及控制端解码仍可能造成卡顿。之前的优化没有完全消除用户感受到的卡顿。
- 不支持依赖 Windows 虚拟显示驱动的「超级屏」。平板切换分辨率时要求 Win10 或报 501 的问题，没有在这条 Wine 路线上解决。
- 实体桌面是 16:9，而控制端比例不同；在保持完整画面、等比显示和本机设置不变的前提下，不能同时去掉所有黑边。控制端若有拉伸或裁剪功能，也会相应变形或丢失部分画面。
- 22.04 迁移文档提供参考，不代表已适配；上游其他后端、控制端组合和功能也没有在本分支全部回归验证。

## 开发检查

下面的针对性测试会创建独立测试桌面，需要本机具备相应 X11/VNC 开发依赖：

```bash
env PYTHONPATH=tests /usr/bin/python3 -m unittest \
  test_ubuntu20_x11_install test_console_focus test_manager_vnc_hover \
  test_manager_tooltip_cleanup test_terminal_runtime
```

历史阶段记录见 [ubuntu-20.04-x11-local.md](docs/ubuntu-20.04-x11-local.md)，部分旧参数已被后续修改替代，以当前脚本和综合适配记录为准。

账号状态、Wine 前缀、令牌、录屏、日志、下载缓存和本机构建目录不随此次源码发布上传。使用和分发第三方软件时，应遵守其各自许可；项目源码许可见 [LICENSE](LICENSE)。
