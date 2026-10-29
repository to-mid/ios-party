# ios-party

小米 MIX 2（chiron）的 XNU / iOS 移植实验工程。目标设备为 6 GB / 128 GB 配置，实测 SoC 修订 2.1、Synaptics 触控、JDI video 面板。

默认发布分支 `codex/hackios-chiron` 保存 HackiOS 8796 路线，`codex/chiron-source-trace` 保留此前 10063 基线；触摸与 SMP 组合源码位于 `codex/chiron-parallel-drivers`。
原始贡献采用 [BSD-2-Clause-Patent](https://github.com/to-mid/ios-party/blob/codex/hackios-chiron/LICENSE)，上游源码和补丁保留各自许可，见[第三方与发布范围说明](https://github.com/to-mid/ios-party/blob/codex/hackios-chiron/NOTICE.md)。仓库及公开构建附件不提供 Apple 原版系统卷。

当前桌面路线基于固定版本的 HackiOS XNU 8796、iOS 16.5 / 20F66 用户态配方及 MSM8998 EDK2，并加入 MIX 2 的启动、USB、内存、触摸和 SMP 适配。早期 UniXNU / DarwinBoot / native 4 KiB 最小用户态路线保留为独立基线；两条路线的启动、ABI 和运行结果分别记录。

## 当前状态

更新于 2026-10-29；最近记录的实机反馈为 2026-10-27。已有 iOS 桌面、真实触摸和八核组合的实机成功反馈，项目仍处于实验阶段。

| 能力 | 已有结果 | 证据与边界 |
| --- | --- | --- |
| iOS 桌面与单核真实触摸 | 实现了真实触摸与可见桌面操作，持续采样及 iOS 消费日志匹配 | 单核构建 `40327776076`；仍明显卡顿，未证明流畅或长期稳定 |
| 双核触摸组合 | 完成编译、打包，实测运行正常 | 构建 `40434498916`；该次实测尚未补充独立运行日志及镜像哈希复核 |
| 八核触摸组合 | 实现了八核触摸组合，实测确认可用；QEMU 八核上线、双簇共享调度集合、SpringBoard 桌面及 540 秒定时返回通过 | 构建 `40457536115`，运行 `40457857698`，保存证据复核 `40459663351`；八核实机结果来自现场观察，尚未补充对应真机日志及运行镜像哈希复核 |
| 系统卷与原生存储 | 当前通过驻留 loader 接收匹配的 APFS 系统卷到 RAM | 不代表 MIX 2 原生 UFS 运行路径可用；原生存储仍待独立验证 |
| 最小 native 4 KiB XNU / 自有 PID 1 | 真机心跳及四项最小用户态自检已有记录 | 属于早期 UniXNU 路线，不替代 HackiOS 桌面、多核或完整系统资格 |

触摸细节见 [Synaptics 记录](research/CHIRON_HACKIOS_TOUCH.zh-CN.md)，多核调度修复、QEMU 与实机观察见 [SMP 记录](research/CHIRON_HACKIOS_SMP.zh-CN.md)。运行证据总览见 [运行状态](RUNTIME_STATUS.md)，快速迭代和交付约定见 [单轮修改与交付](research/CHIRON_ITERATION.zh-CN.md)。

## 实验发布与构建入口

已发布 [单核 / 双核 / 八核触摸实验包](https://github.com/to-mid/ios-party/releases/tag/chiron-touch-smp-20261027)，原 [单核触摸发布](https://github.com/to-mid/ios-party/releases/tag/chiron-touch-20261026) 保留。各包具有独立的镜像、manifest、上传脚本、说明和校验文件；八核包另含 QEMU 复核与桌面截图。

发布时的八核说明写作“真机待验”；后续 2026-10-27 实测确认最新八核可用，补充证据记录在 SMP 文档中。当前文件更新不自动修改 GitHub Release 正文或 UniXNU 官网。

附件不包含 Apple 大系统卷、私人设备日志或凭据。使用者自行准备匹配的 `system.apfs`，按包内说明核验并操作；手机约 300 秒返回 loader 是实验设置。发布不代表日常可用、流畅拖动或长期稳定。

| 修改范围 | 现有入口 | 源码分支 |
| --- | --- | --- |
| HackiOS 原配方云端参考验证 | `.github/workflows/hackios-reference.yml` | `codex/hackios-8796-20f66` |
| MIX 2 基础平台内核与 loader | `.github/workflows/hackios-chiron.yml` | `codex/hackios-chiron` |
| 触摸、SMP 及组合源码构建 | `.github/workflows/hackios-chiron-drivers.yml` | `codex/chiron-parallel-drivers` |
| 基础候选 / 驱动实验打包 | `.github/workflows/hackios-chiron-package.yml` / `hackios-chiron-driver-images.yml` | `codex/hackios-chiron` / `codex/chiron-parallel-drivers` |
| 八核双簇独立 QEMU 与保存证据复核 | `.github/workflows/hackios-chiron-smp-qemu.yml` | `codex/chiron-parallel-drivers` |
| HK 系列本地校验与交付 | `tools/deliver_chiron_hackios.py` | 本地交付工具，未随本次文档同步发布 |

完整内核编译和运行验证在云端执行，本机仅做轻量检查、必要产物下载与交付校验。设备临时启动、系统上传、刷写和重启按说明手动执行。

## 早期路线记录（截至 2026-10-23）

以下为早期 UniXNU / RAM init 和首次 HackiOS 候选的阶段快照，后续触摸、多核及发布状态以上方总览和对应模块记录为准。

2026-10-23，独立 HackiOS HK6c 路线实测出现苹果 Logo 后进入桌面，触摸无响应；本次仅有现场观察记录。该实机基线使用单核，未据此授予交互或长期稳定性资格；[Synaptics 触摸](research/CHIRON_HACKIOS_TOUCH.zh-CN.md) 与 [SMP](research/CHIRON_HACKIOS_SMP.zh-CN.md) 已按计划分别推进。多核运行 `39964860075` 的新内核与 loader 均完成链接和产物复核，双核、八核独立临时镜像经运行 `40053115918` 打包并完成本地静态复核，交付于 `dist/chiron-driver-candidates/`，尚无实机多核运行结果。上游导出名单检查限制见 SMP 记录，反馈边界见 [HK6c 记录](research/CHIRON_HACKIOS_MEMORY.zh-CN.md#hk6c-桌面反馈与并行外设工作)。

触摸的原生 QUP/F12、条件接管和只读板级探针已完成源码实现；当前探针内核经运行 `40056688514` 链接，诊断镜像经 `40057611818` 打包并本地复核，交付在 `dist/chiron-driver-candidates/TOUCH-PROBE-40057611818/`。它会在 GUI 前输出寄存器快照并主动 panic，用于取得实机接管状态，尚未声称触摸可用。

2026-10-22，实机照片 `IMG_20261022_162407.jpg` 已显示构建 `CT-39745321134-1-783CC7E68F24` 和多轮 `CHIRON RAM init: PID 1, 4K ABI, heartbeat`，确认 MIX 2 上的最小 4 KiB XNU 与自有用户态 PID 1 已运行。该自有 RAM init 路线仅覆盖最小用户态，不代表完整 iOS 或可日常使用的系统。早期 CPU 页大小能力与 GOP 参数见 [UEFI 探针记录](research/CHIRON_UEFI_PROBE.zh-CN.md)。

此前已在 Cortex-A53 QEMU 中观察到自有 PID 1、4 KiB ABI 和两次真实心跳，并通过实机 `17` 标记确认原始 `arm_vm_init` 返回。当前实机多轮心跳进一步支持 PID/页大小检查、控制台写入和多轮定时等待返回；单张照片不证明长期稳定性，也不单独确定此前停滞的根因。

后续主线改为固定源码基线上的统一日志：入口与内存初始化记录、持续内核文本、EL1 异常 PC/ESR/FAR 和 panic 原因先进入版本化内存记录，再输出到屏幕。云端 `ram-trace` 模式统一编译、验证、编号和打包，不再对编译后的 XNU 追加指令补丁。以前的本地插桩工具保留为历史诊断资料。

2026-10-22 的 `IMG_20261022_151546.jpg` 已显示该源码日志，最后可见 `PE_create_console() enter`；`arm_vm_init`、独立原生 framebuffer 映射和串口初始化均已返回，尚无实机 PID 1。后续核对旧成品指令发现控制台路径仍读取 BCM2837 固定 panic 区域 `0x3c0fc000`；RAM 运行配置已改为仅使用经校验的设备树区域，新增控制台内部日志和 QEMU 早期资格。不能把此配置问题直接认定为照片中的停滞原因。

修正后的云构建 `39745321134` 已通过控制台路径、POST 正反例、PID 1 两次心跳及真实 panic/数据异常资格，并完成打包及本地原始记录/载荷哈希复核。实机心跳对应镜像为 `dist/source-trace-39745321134/CT-39745321134-1-783cc7e68f24.img`，照片证据与完整边界见 [源码可观测性](research/CHIRON_SOURCE_TRACE.zh-CN.md)。

设备刷写、临时启动和重启按说明手动执行；源码、构建、产物校验和日志分析分别记录。

下一阶段已并行实现：[用户态自检](research/CHIRON_USER_SELFTEST.zh-CN.md) 已在真实 XNU/QEMU 完成文件读取、两页匿名映射、独立子程序执行及退出回收。旧候选实机出现 SIGBUS 后，已修正入口栈对齐并验证持久故障快照；修正版 `dist/source-trace-39770335838/CT-39770335838-1-775f3f2e4009.img` 完成云端及本地核验后，实机照片 `IMG_20261022_195153.jpg` 已显示相同构建号和多轮 `HEARTBEAT SELFTEST PASS`，支持四项最小用户态自检在 MIX 2 通过，详见 [SIGBUS 排查](research/CHIRON_SIGBUS_DEBUG.zh-CN.md)。单张照片不证明长期稳定性或旧故障的精确根因。[UFS 只读探针](research/CHIRON_UFS_READONLY.zh-CN.md) 已通过宿主测试和独立 XNU 归档编译；未链接、未执行硬件，仍缺退出 UEFI 后的电源/时钟、链路、DMA/SMMU 及独占交接资格。

首轮内核、驱动库、EFI 和整合固件的远程编译均已通过。成品位置、哈希和验证边界见 [构建记录](BUILD_STATUS.md)。

## 早期 UniXNU / RAM init 构建

本节保留早期路线的构建方式，HackiOS 桌面与触摸/SMP 候选使用上方独立入口。GitHub Actions 工作流 `Chiron remote bring-up` 在源码推送时仅跑轻量验证；真正的编译任务仅手动触发：

- Linux runner 编译 AArch64 UniXNU、DarwinBoot 和 MSM8998 固件。
- macOS runner 编译 4 KiB XNU 源内核与平台驱动静态库。
- Mac 本地只运行轻量测试、生成 ADT 和下载必要产物。
- 不下载完整 IPSW，不生成 userdata 镜像，不执行 ADB/fastboot。
- 每个任务设有超时，产物保留 7 天；不缓存完整编译树。

固件集成重跑可填写 `efi_run_id`，复用同仓库此前成功生成的 `chiron-efi` 产物；该情况下跳过 EFI 重编译。更新 EFI 源码后应留空，重新编译。

`kernel_mode=ram-init` 增加源密码学、pthread、平台/CPU 与 mockfs 链接，并运行 QEMU POST 正反例和自有 PID 1 心跳测试。该模式需填写可用的 `efi_run_id`；其通过标准是 `runtime-test.json` 的真实运行观察，不能以编译成功代替。源密码学使用上游固定的第三方评价快照，来源信息保留在 `ci/sources.json` 和构建报告中，不声明厂商认证。

源码诊断配置关闭依赖原始 AppleImage4/AMFI 的完整 trust-cache runtime；自有 init 仍进行构建时 ad-hoc 签名检查，但不声明 Apple 信任链或完整 iOS 安全模型。完整系统路径需要另行接入真实组件。

仅在显式的 `XNU_DIAGNOSTIC_EMPTY_ENTITLEMENTS` 配置中，AMFI 缺失且签名不含 entitlement 上下文、XML/DER blob、entitlement special slot 时，空集合的加速操作返回成功；带 entitlement 的签名返回不支持。该诊断分支保留正常签名结构和页哈希路径，不能作为完整 AMFI 的替代品。

同一诊断模式下，AMFI 缺失时的 IOKit entitlement 查询返回空值；它不授予任何 entitlement。存在 AMFI 时仍执行原来的查询实现。

新增 RAM 负载通道通过 bootloader 的 `linux,initrd-start/end` 交接只读 FAT32 卷，早期保留 payload、固件卷和 ramoops，并通过 GUID HOB 传给 `ChironRamdisk`。该路径正在验证；它不需要写 userdata 或新增磁盘分区。

`ram_init_run_id` 与 `firmware_run_id` 同时填写时，独立打包任务会校验运行观察与输入哈希、创建 48 MiB RAM 卷、读回校验文件，再生成 `chiron-ram-init-experimental.img`。固件输入必须是包含新 RAM 卷驱动的构建，不能使用最初的纯探针固件。该镜像仍需独立实机验证。

本地验证：

```sh
python3 -m unittest discover -s tests -v
python3 scripts/make-chiron-adt.py --output out/chiron/chiron.adt
```

## 早期 bootstrap 产物的边界

`configs/chiron_defconfig` 是第一轮源代码 bootstrap 配置。CPU、平台和密码学等外部库尚未链接进内核；原始内核不能直接作为 DarwinBoot 的完整 XNU payload 使用。

CI 另行编译 ArmDTPlatform 与 ArmCpu 静态库。其中 ArmCpu 已改为从 ADT 的 `virtual-timer-intid` 读取本机虚拟定时器中断号 19，保留上游 QEMU 的默认值 27。

`ChironProbe.efi` 是无存储访问的应用级探针，显示原始 CPU 特性、GOP 和 UEFI 内存图。它不切换页表、不退出 boot services；固件侧键入口指向该探针，默认启动仍由 UniXNU registrar 注册。整个固件仍含 vendor 驱动，不能据此声称全链路无写入。

ADT 使用真实 chiron 身份、单核、SMC、GICv3 和易失 NVRAM，不包含存储节点和伪造的 Apple 设备身份。它向 DarwinBoot 请求 1 GiB 实际 UEFI 分配，而不把 Android 的候选地址硬编码为可用窗口。

旧 MSM8998 固件与新加载器分别用各自固定的 EDK II 基线编译，再把 EFI 二进制嵌入固件。编译成功仍需验证 UEFI 协议、最终内存图与入口行为；原平台含未审计的 vendor 驱动，编译产物不能因此称为无写入的诊断镜像。

## 目录

| 路径 | 用途 |
| --- | --- |
| `board.toml` | 实测板级参数与当前构建阶段 |
| `research/chiron-live-profile.json` | 去除设备标识的运行时测量结果 |
| `scripts/make-chiron-adt.py` | 最小 Apple Device Tree 生成器 |
| `drivers/ArmCpu/` | 上游 BSD 驱动及 chiron 定时器适配 |
| `ci/sources.json` | 固定的上游源码提交 |
| `ci/hackios/` | HackiOS 平台、触摸、SMP 覆盖层及构建/打包实现 |
| `drivers/chiron-touch/` | QUP / Synaptics DSX 协议、板级接管与 XNU 适配 |
| `research/hackios-reuse/sources.lock.json` | HackiOS、ios-sweet 参考及 XNU 的固定来源版本 |
| `ci/` | 远程构建、集成与产物校验 |
| `tests/` | ADT 资源、NVRAM 和产物架构检查 |
| `tools/` | boot 镜像解析、ADB 只读采集和 FDT/GPT 校验 |
| `research/` | 移植评估、静态镜像分析及实机观察记录 |

大文件、运行时原始采集、设备标识、Apple 镜像与本地构建目录均不进入 Git。上游驱动的许可保存在 `drivers/ArmCpu/LICENSE`。

## 研究记录

- [当前运行状态](RUNTIME_STATUS.md)
- [Synaptics 触摸与实机反馈](research/CHIRON_HACKIOS_TOUCH.zh-CN.md)
- [SMP、双簇调度及八核运行证据](research/CHIRON_HACKIOS_SMP.zh-CN.md)
- [USB 接管](research/CHIRON_HACKIOS_USB.zh-CN.md)
- [连续内存窗口与首次桌面反馈](research/CHIRON_HACKIOS_MEMORY.zh-CN.md)
- [单轮修改与交付](research/CHIRON_ITERATION.zh-CN.md)
- [移植评估](research/MIX2_PORTING.zh-CN.md)
- [boot.img 静态分析](research/CHIRON_BOOT_ANALYSIS.zh-CN.md)
- [ADB 实机采集分析](research/CHIRON_LIVE_ANALYSIS.zh-CN.md)
- [UEFI 探针照片转录](research/CHIRON_UEFI_PROBE.zh-CN.md)

本地 `research/upstream/` 仅作为参考快照，不上传；上游入口和对应版本记录在移植评估中。重新采集 Android 数据时，将输出保存在 `research/artifacts/`，该目录被 Git 忽略。
