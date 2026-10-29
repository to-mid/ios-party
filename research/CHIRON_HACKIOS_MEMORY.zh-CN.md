# HK5 实机 USB 通过后的连续内存问题

## 2026-10-23 实际观察

实机照片 `IMG_20261023_190807.jpg` 显示 HK5 已停止 host，
创建并绑定专用 DWC3 外设句柄，取得 USB Function IO，loader 打印
`usb: started`。运行上传工具后，loader 返回容量
1,191,182,336 字节，低于固定系统卷的 2,492,219,392 字节。
容量检查阻止了上传，XNU 没有执行。

本会话通过已枚举的 `xnuloader` 只读查询确认：

```text
product: xnuloader
board: chiron
max-download-size: 0x47000000
```

随后只运行 `oem memmap`、`oem log` 和 device-to-host 的
`get_staged`，没有 stage、run、boot、flash 或 reboot。
完整日志保存于 `research/hackios-reuse/hk5-memory-map-20261023.txt`。
该摘要打印至少 1 MiB 的区间和各类型总量，不是逐项原始描述符；
HK6 loader 的运行时核验使用完整 GetMemoryMap 描述符。
这确认了 USB 控制通道和日志读回，不等于多 GB 传输已通过。

关键内存区间：

| 区间 | 实际类型 / 意义 |
| --- | --- |
| `0x100000000–0x16e000000` | EfiLoaderData，上传工具的 oem ramdisk 已领取的 1.71875 GiB 窗口 |
| `0x16e000000–0x170000000` | 早期 UEFI 区域的后段；有空闲碎片和仍活跃的 Boot Services |
| `0x170000000–0x1b75b6000` | 约 1.11 GiB 的另一段连续空闲内存 |
| `0x1b75b6000` 以上 | 混合的 Runtime、Boot Services 和小段空闲内存 |

loader 选择最大的单段 EfiConventionalMemory，领取后缓存窗口；
UEFI 和 USB 驱动在 XNU 执行期间仍驻留，所以不能回收活跃固件内存，
也不能将两段空闲内存简单相加后按连续 RAM 使用。

现有预算为：

```text
0x6e000000 window
- 0x08000000 resident offset
- 0x1f000000 kernel reserve
= 0x47000000 root capacity
```

原始早期平台配置 PcdSystemMemoryBase=`0x80000000`、
PcdSystemMemorySize=`0xf0000000`，计算的系统顶端为 `0x170000000`；
固定依赖的 PrePi 区域默认是 128 MiB，起点为 `0x168000000`；
其中实际分配的后段与实机碎片位置对应。
HK6 首轮误按 32 MiB 写入 stage 1 检查，实机照片
`IMG_20261023_200326.jpg` 已确认该检查失败，未领取高 bank。

## HK6 的所有权方案

只重建 UEFI、入口和 loader，复用既有源码内核与原系统卷。
保留 HK5 镜像及其 USB 实机记录。

1. 将早期 PcdSystemMemorySize 改为 `0x60000000`，
   保留原有 128 MiB 大小，使 PrePi 堆栈放到 `0xd8000000–0xe0000000`。
   这是早期区域放置参数；实际 RAM resource HOB 仍按 FDT 的 6 GiB 配置生成。
2. 在平台 resource/allocation HOB 已生成、DXE 分配开始前，
   核验早期堆位置、FDT bank、固定 reserved-memory、动态池 alloc-ranges
   及现有非 Conventional allocation HOB。
3. 仅在整段通过检查时，为
   `0x100000000–0x1bc000000` 建立 EfiLoaderData allocation HOB，
   并记录专用 GUID HOB。固件其他分配不能再占用该窗口。
4. EFI 入口仅在 HOB 的 magic、版本、范围、status 和 stage 有效时
   发布窗口协议。
5. loader 再检查当前完整内存映射：整个窗口必须由非 Runtime 的
   EfiLoaderData 覆盖，不能有空洞、Conventional 或活跃 Boot Services。
   通过后使用已领取窗口，不重复 AllocatePages。
6. 保留用户工具和 loader 的上传长度上限。

预期可用容量：

```text
0xbc000000 window
- 0x08000000 resident offset
- 0x1f000000 kernel reserve
= 0x95000000 = 2,499,805,184 bytes
```

固定系统卷为 `0x948c4000`，在上限内，余量 7,585,792 字节。
内核运行预留仍是原来 A53/4K/high-bank 云端桌面检查使用的数值；
此次没有通过减小预留或跳过容量检查来强行上传。

## 固件与传输

固定平台源码仍为 `edk2-porting/edk2-msm8998`
`ace27110b9b9b6c591387d50cf748ec646402b4a`。
复用运行 `39417919101` 的四个原 EFI 模块，
从既有源码重建只读 ChironRamdisk 与 HK6 入口。
内存变化由 `ci/hackios/apply_memory_policy.py` 应用到云端 scratch 源码。

Android boot v0 header 与 DTB 来自已验证模板。
新编译 UEFI body 从 `0x8000` 开始替换；模板中由源码组装、已实机验证的
前 `0x8000` transport prefix 保留，仍严格检查 24 MiB 物理传输并补齐零尾部。
打包器拒绝尺寸、原始 Image header、前缀空白或固件哈希不符的重建结果。
新固件体不是旧固件字节不变的延续，实机资格保持 false。

## 已做检查

- 7 项宿主检查通过，涵盖窗口覆盖、空洞、错误内存类型、
  Runtime 属性、无效范围与 stride、传输前缀和 DTB 保留，以及 HK5 USB 失败路径。
- 依据固定上游重放 loader 补丁并通过 AArch64/Windows freestanding 语法检查。
- 内存策略源码 delta 已在固定平台源码副本上应用。
- 模板 DTB 中的固定保留区均在窗口外；动态 DMA 池的 alloc-ranges 均限制在 4 GiB 以下。

源码提交 `4d5c841543363a6f16b39eab9d84c37555a8bfe5`，
云端构建运行 `39947141008` 的入口、原平台固件和镜像三阶段全部通过。
7 项宿主检查也在云端通过；未重新编译 XNU 或下载系统卷。

## 交付复核

| 对象 | SHA-256 |
| --- | --- |
| HK6 启动镜像 | `8badb258879bfe749540fa24d23a01217776dfc9b9143d9bd6821a66b1ea61e4` |
| 新入口，18,432 字节 | `d188beb5dd189cf74f6e928228808b94a60ff82688c7725775532ea7e2401c32` |
| 新 loader，70,144 字节 | `405ef510b9bbab54921af06c4a7547901e89b3e27c800a049e2153eacade5e37` |
| 云端编译的原始 6 MiB FD | `d34c4ee28886779371adbc64dd39efbb918c6c9f8659ef3f827b1ab982706abc` |
| 保留 transport prefix 后的有效 FD | `9ef9d56b081ada9efc4f337bdb4c4ecb79f8c824cba183e29f265558acca6db0` |

本机独立重算容器 SHA-256、legacy SHA-1 和 FAT 文件哈希；
逐字节比较前缀、DTB 与 HK5 相同、新固件体与编译结果相同、
内核载荷与 HK5 相同。四份冻结 EFI 模块哈希与基线一致，
ChironRamdisk 与新编译 artifact/integration report 一致。

从固定来源重放内存策略与 loader 改动，四份固件源文件及两份 loader
源文件哈希都与云端记录匹配。系统卷清单与 HK5 相同；
上传时工具仍会计算实际系统卷完整哈希。

交付目录 `dist/mix2-hackios/`：
`MIX2-HackiOS-HK6.img`、`manifest-hk6.json`、
`local-verification-hk6.json`、`START-HK6.zh-CN.md`。
原始产物为 `dist/mix2-hackios-hk6-build/`，
完整云日志为 `out/chiron-port/package-hk6-39947141008.log`。
精简验证记录为 `research/hackios-reuse/phone-memory-hk6-20261023.json`。

上述是 HK6 交付前的检查记录；后续实机在 stage 1 停止。
实际连续窗口、根卷容量及 XNU 执行均未通过，
没有将源码编译或宿主检查记为手机启动成功。

## HK6b 快速修正

HK6 后续实机照片确认 stage 1 Unsupported。已核对父仓库的 EDK2 gitlink
`e7aac7fc137e247edad22f7ee53b9a1fba227397`：
`ArmPlatformPkg/ArmPlatformPkg.dec` 中的
PcdSystemMemoryUefiRegionSize 默认为 `0x08000000`，不是 `0x02000000`。
HK6b 修正固定堆基址为 `0xd8000000`、大小为 `0x08000000`，
保留所有不重叠、FDT、HOB 和 loader 全量内存覆盖核验。

新增实际 128 MiB 堆通过、错误 32 MiB 堆与高 bank 重叠堆拒绝的回归用例。
8 项宿主检查通过。复用运行 39947141008 的入口、只读 RAM 驱动及 loader，
只重建固件体和打包；原系统卷及源码内核不变。
修正提交 `93eb888fde83b03b8cbc10ee759697f4b15a6c8b`，
构建运行 `39951169904`。

## HK6b 运行时映射与 HK6c

实机照片 `IMG_20261023_202356.jpg` 及只读取得的
`hk6b-window-failure-20261023.txt` 确认：
前序检查已通过并进入 USB loader，但日志打印
`preclaimed memory ownership check failed 0`。
实际映射为 `0x100000000–0x1bd8c0000` 全段 ConventionalMemory/type 7，
不存在 HK5 的中间活跃 UEFI 分配；早期区域搬移已生效。
HOB 意向声明没有在最终映射中体现为 LoaderData，不能据此直接视为已领取。

HK6c 仅更新 loader：核验目标全段为空闲 type 7 后调用
AllocatePages(AllocateAddress, EfiLoaderData)，再取完整映射要求全段 type 2、
无 Runtime 属性、无空洞；复查失败释放本次领取的页，不设置 win_size。
初次遇到其他 LoaderData 也不会直接当作自己已拥有。
9 项宿主检查通过，新增实际领取函数的 11 个情景，覆盖前检、领取、
后检及回滚。云端首轮被测试替身的 GCC 生命周期告警阻止，
测试服务表改为 static 后，运行 `39955334579` 全部通过。
入口及固件复用 HK6b，不重建内核或系统卷。

## HK6c 桌面反馈与并行外设工作

2026-10-23，HK6c 实测观察到“出现一个苹果 logo，然后进桌面”，
同时反馈触摸无响应。本次为现场观察记录，没有新增照片、内核日志或
设备侧镜像哈希；据此记录为实机可见桌面反馈，不将其扩展为交互、
长期稳定性或多核资格。原始构建清单保留生成时的资格状态。

当前交付仍明确使用 `cpus=1`、`cpu_count=1` 和 loader 启动核限制。
触摸尚未接通 chiron 的 Synaptics DSX 到现有 HID 事件链路。
后续计划并行处理触摸与多核，两项分别实施与验证；
HK6c 保留为可回退的桌面基线，实验源码及验证边界分别记录于
`CHIRON_HACKIOS_TOUCH.zh-CN.md` 和 `CHIRON_HACKIOS_SMP.zh-CN.md`。

后续并行工作已经生成独立的 SMP2/SMP8 源码实验镜像（打包运行
`40053115918`），保留 HK6c 及原有 latest-candidate 记录。其固件、DTB 和
USB 入口与 HK6c 相同，新内核、loader 与 CPU 参数分别校验；截至交付没有
多核实机运行结果，也没有将触摸实现状态记为硬件可用。
