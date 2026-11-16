# 只在一部手机上使用，不需要电脑

nanoMuse 可以只活在一部 Android 手机上：智能体、数据以及回答问题的模型都在手机里。不需要电脑，不需要中继，也不需要账号。第一次设置完成后，没有网络也能用。本页说明两种做法、各自能做什么，以及原来跑 nanoMuse 的电脑已经不在了怎么办。

第一次设置要下载软件和一个模型，所以需要一次 Wi-Fi 或移动网络。模型有好几 GB；装进手机之后就不再下载。

如果电脑上已经有这个模型，可以复制过来，不必重新下载：Ollama 的模型放在数据目录下的 `models` 文件夹里（Windows 是 `C:\Users\<你>\.ollama`，macOS 和 Linux 是 `~/.ollama`）。把整个 `models` 文件夹复制到手机 Termux 的 `~/.ollama/models`，安装时加 `--skip-model`。模型清单和二进制块可以跨平台使用，Ollama 不用联网就能找到它。用移动网络时这是最快的办法。

## 需要准备

- 一部 arm64、Android 8.0 或更新的手机。默认的 4B 模型以 6 GB 内存比较合适；4 GB 手机可以用 3B 模型；10 GB 以上可以用 8B 模型。
- 从 F-Droid 安装 [Termux](https://f-droid.org/packages/com.termux/)，不要用 Google Play 的版本。Play 版本很旧，软件包对不上。
- 默认设置大约需要 4 GB 空闲存储，文件和聊天记录另算。
- 可选：[Termux:Boot](https://f-droid.org/packages/com.termux.boot/) 让手机重启后自动启动 nanoMuse；[Termux:API](https://f-droid.org/packages/com.termux.api/) 让提醒和提问出现在手机的通知栏。
- iPhone 不能在本机跑服务：iOS 不允许应用长期保留一个本地服务。iPhone 需要另一台设备上的模型接口、中继，或者自己的 key。本页其余部分讲 Android。

## 两种带在身上的方式

| | 应用加手机上的模型 | 运行时加网页版 |
|---|---|---|
| 手机上跑什么 | nanoMuse 应用自己的智能体，以及提供模型的 Ollama | Ollama、Python 运行时，以及浏览器里的网页版 |
| 你看到什么 | Android 应用，包括“手”、通知和手机工具 | 和电脑上一样的网页版，可以添加到主屏幕 |
| 适合谁 | 没有历史数据的新手机；要用手机自己的日历、联系人、闹钟和屏幕 | 想保留电脑上的聊天、目标、记忆和文件；想用网页版 |
| 数据在哪 | 应用自己的数据；登录后还会走账号同步 | 手机上的 `~/.nanomuse` 和工作区 |

两种方式都用 [Ollama](https://ollama.com) 当模型服务。它跑在手机里，提供的接口和应用、运行时本来就会用的 OpenAI 兼容接口一致。

## 方式 A：应用加手机上的模型

从[最新版本](https://github.com/nano-muse/nanoMuse/releases/latest)安装应用。不登录也能用自己的模型；只有用中继的模型、同步和设备互联时才需要账号。

在 Termux 里，一条命令安装 Ollama 和一个适合这台手机的模型：

```bash
curl -fsSL https://raw.githubusercontent.com/nano-muse/nanoMuse/main/scripts/phone/install.sh | bash -s -- --app
```

脚本会按手机内存挑选最大的模型，下载并启动 Ollama。想换模型就加 `--model`：

```bash
curl -fsSL https://raw.githubusercontent.com/nano-muse/nanoMuse/main/scripts/phone/install.sh \
  | bash -s -- --app --model qwen2.5vl:3b
```

然后在应用里：

1. 打开“设置、模型、对话、添加服务商、Ollama”。
2. 地址保留 `http://127.0.0.1:11434/v1`，选择安装脚本打印的模型，例如 `qwen3:4b`。
3. 要用“操作屏幕”，再添加一个能看图的模型，例如 `qwen2.5vl:3b`。

这个地址就是手机本身。应用通过回环接口把对话发给 Ollama，别的设备和别的应用都读不到。

用应用的时候让 Termux 留在后台。Android 会自己停掉后台应用，需要放行：打开系统的电池设置，找到 Termux，选择“不受限制”（不同厂商的叫法不一样）。装上 Termux:Boot，重启后 Ollama 会自己回来；想让应用和运行时的通知进入通知栏，就保留 Termux:API。应用自己的通知不需要 Termux:API。

## 方式 B：运行时和网页版都在手机上

这种方式跑的是和电脑上一样的 Python 运行时，聊天、目标、记忆、动态、技能和网页版都一样。电脑已经不在了还想继续用它的数据时，这是可行的办法，前提是先把数据导出。

在 Termux 里：

```bash
curl -fsSL https://raw.githubusercontent.com/nano-muse/nanoMuse/main/install.sh | bash
```

安装脚本会：

- 安装 Ollama 和一个适合这台手机的模型；
- 用 `proot-distro` 装一个小型 Debian 容器，把 Python 运行时装进去；
- 让运行时连手机上的 Ollama，并且只监听 `127.0.0.1`；
- 写好 `nanomuse-phone` 命令；装了 Termux:Boot 时还会写开机脚本。

结束后，在手机浏览器打开 `http://127.0.0.1:8787/`，选择“添加到主屏幕”。网页版由手机提供，service worker 会缓存界面，所以没有网络也能打开。访问令牌仍然开启：浏览器会记住它，服务器只监听回环地址。

想从本地代码目录安装，例如带自己改动的分支：

```bash
bash scripts/phone/install.sh --runtime --source /sdcard/nanoMuse/repo
```

代码会复制进 Debian 容器，之后手机存储里不需要再留着这个仓库。加 `--repo URL` 可以改成克隆另一个仓库。

### 命令

安装脚本会把 `nanomuse-phone` 放进 Termux 的 `PATH`：

| 命令 | 做什么 |
|---|---|
| `nanomuse-phone up` | 启动 Ollama 和运行时，并申请 Termux 唤醒锁 |
| `nanomuse-phone down` | 停止两者 |
| `nanomuse-phone restart` | 停止后再启动 |
| `nanomuse-phone status` | 查看模型和应用是否有响应 |
| `nanomuse-phone open` | 打开 `http://127.0.0.1:8787/` |
| `nanomuse-phone logs` | 查看运行时和 Ollama 日志的最后几行 |
| `nanomuse-phone update` | 拉取源码、重新安装并重启 |
| `nanomuse-phone model NAME` | 下载另一个 Ollama 模型、切换过去并重启 |
| `nanomuse-phone restore FILE` | 停止应用、恢复数据导出、再启动 |

一个小守护程序会在运行时退出后把它拉起来；唤醒锁会请 Android 在它工作时别停掉 Termux。内存紧张或者厂商的电池管理很激进时，Android 仍然可能停掉 Termux。应用没有响应时，打开 Termux 运行 `nanomuse-phone up` 即可。

### 恢复电脑上的数据

电脑还在的时候，在网页版打开“设置、下载你的智能体数据”，把 zip 拷到手机。先在 Termux 里运行一次 `termux-setup-storage` 并允许访问，让 Termux 能读“下载”目录，然后运行：

```bash
nanomuse-phone restore ~/storage/downloads/nanoMuse-data.zip
```

恢复只会接受 nanoMuse 导出文件里本来就有的名字：聊天、会话、记忆、目标、提醒、触发器、资料、动态、点子、联系人、技能、轨迹和审计日志。它不会替换手机上的 `config.toml`、保险库或服务器令牌。导出文件本来就不含密钥和设置，所以 API key 和连接器要在应用里重新填。工作区文件也不在导出里：想保留就把电脑上的 `workspace` 文件夹拷到手机上的 `~/.nanomuse/workspace`。

如果电脑已经坏了，之前也没有导出，那台电脑上的聊天和记忆无法找回。本页不能把它们变回来；它只保证下一部手机不再依赖一台电脑。

上面的导出不含 One For All 的数据库、工作区文件、保险库和服务器令牌。想整套搬走，就在电脑上打包：

```bash
nanomuse phone package --out nanoMuse-bundle.tar.gz
```

把 `nanoMuse-bundle.tar.gz` 和 `scripts/phone/install.sh` 拷到手机，然后在 Termux 里：

```bash
bash scripts/phone/install.sh --runtime --bundle /sdcard/Download/nanoMuse-bundle.tar.gz --model qwen3:4b
```

这个打包文件带着代码、聊天、记忆、目标、工作区文件、保险库和设置，手机打开后和电脑上一样。用 `--model` 选择手机要跑的模型；电脑上的大模型在手机上可能跑不动。

## 两个运行时直接配对，不需要中继

两个运行时可以直接互相同步聊天：不需要中继，不需要账号。在两台设备的终端里分别运行：

```bash
nanomuse pair add http://192.168.1.20:8787 --token <另一台设备的 server_token>
nanomuse pair list
nanomuse pair remove <名字>
```

令牌存进加密保险库，`pairs.json` 只记录地址和保险库引用。两台设备都在线时（同一个 Wi-Fi，或任何能到达该地址的网络），聊天会自动同步：各自把新消息推给对方，同时拉取对方的新内容，主聊天是这对设备共用的一个对话，改名和删除也会同步。地址就是另一台设备上 `nanomuse serve` 打印的地址，令牌在那台设备的 `data/server_token` 里。每个运行时都提供和中继一样的同步接口，所以配对后的效果和账号同步一样，只是中间不再有服务器。

## 离线时能做什么，什么还需要网络

按上面的方式设置好之后，没有网络时：

- 和本地模型聊天；选的模型能看图时，也能发图片。
- 记忆、目标、提醒、例程、动态、点子、文件、技能和资源库。
- 网页版的界面和保存过的页面。service worker 还会保留 API 读取的最后一份好数据，所以断网冷启动也能打开。
- Termux 还活着时，通过 Termux:API 发出的通知。Web Push 需要互联网上的推送服务，手机单独运行时不用它。

按性质仍然需要网络的有：

- nanoMuse Cloud 的模型、中继、登录、对话同步和设备互联。
- 邮件、日历订阅、来自服务器的联系人、网页搜索和更新检查。
- 还没下载完的模型。
- Termux 软件包更新和 `nanomuse-phone update`。

手机单独运行的配置会关闭更新检查，把中继设为不需要，关闭对话同步和设备互联，这样运行时不会去等一个没有的网络。

## 内存、电量和通知

- 4B 模型回答时大约需要 3 GB 内存；8B 模型大约需要 6 GB。Android 可能会为了腾地方停掉别的应用，关掉占内存的大应用会有帮助。
- 唤醒锁让处理器在定时任务到点时可用，会耗电。`nanomuse-phone down` 会释放它。
- Termux:Boot 开机运行 `~/.termux/boot/nanomuse.sh`。安装脚本默认会写这个文件，加 `--no-boot` 就不写。
- `termux-notification` 需要安装 Termux:API。没有它运行时照样工作，只是通知留在网页版里。
- 提醒来晚了，常见原因是 Android 的电池优化。把 Termux 设成“不受限制”。

## 更新和卸载

- 更新运行时：`nanomuse-phone update`。它会拉取当初安装的源码、装回同一个容器并重启。用 `--source` 拷进去的副本没有 git 历史；再用同样的 `--source` 跑一次安装脚本即可更新。
- 更新模型：`nanomuse-phone model qwen3:4b`（或别的名字）。
- 只删运行时、保留数据：先 `nanomuse-phone down`，再 `proot-distro remove debian`。数据在 `~/.nanomuse`；这条命令不会删应用和模型。
- 全部删除：删掉 Debian 容器、`~/.nanomuse`，以及 Ollama 的模型（`ollama rm NAME`）。Termux 和应用之外没有装别的东西。

## 安全

- 网页版和模型只监听 `127.0.0.1`。同一个 Wi-Fi 上的其他设备访问不到。
- 服务器把访问令牌放在 `~/.nanomuse/server_token`；在手机上的脚本里调用 API 时，把它当作 bearer token。
- Termux 的存储只有 Termux 应用能读。Debian 容器和运行时都在它下面。
- 手机上的模型会看到你发给它的每条消息。它在你手机里，由你控制；除非你自己添加服务商，否则没有内容会发给别人。
