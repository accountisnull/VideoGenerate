# CosyVoice3 本地 TTS 与音色克隆接入调研

- 核验日期：2026-09-21。
- 目的：为首期 PRD 的 TTS 选型提供依据；本次仅阅读官方资料和代码，没有安装依赖、下载模型权重或运行推理。
- 结论：可以将本地 CosyVoice3 作为配音服务接入。官方明确提供零样本音色克隆能力；本机运行是否稳定、实际耗时和资源占用仍需部署实测。TTS 输出音频，数字人嘴型同步由后续视频服务完成。

## 1. 已核实的能力

| 问题 | 官方证据与结论 |
| --- | --- |
| 精确模型 | 官方推荐的开源模型标识为 `FunAudioLLM/Fun-CosyVoice3-0.5B-2512`，示例下载目录为 `pretrained_models/Fun-CosyVoice3-0.5B`。目录简称与模型完整版本号不同，应记录完整版本。[S1][S2] |
| 是否支持克隆 | README 明确支持 multilingual/cross-lingual zero-shot voice cloning，包含中文。首期普通话配音符合能力方向。[S1] |
| 是否需要训练 | 官方 CosyVoice3 示例加载预训练模型后，直接调用 `inference_zero_shot`，输入待合成文本、参考音频对应文本和参考音频，不包含训练步骤。按该路径接入不需要为每个音色训练模型；训练/微调是另外的高级能力。[S2][S3] |
| 参考文本 | 普通 zero-shot 示例提供参考音频转写文本，并在 CosyVoice3 中使用 `You are a helpful assistant.<\|endofprompt\|>` 前缀。适配层应按实际版本处理前缀，用户只录入参考音频对应文字。跨语言模式有仅提供参考音频的接口，不能据此省略首期普通 zero-shot 流程的参考文本。[S3][S4] |
| 参考音频时长 | 当前前端代码 `_extract_speech_token` 将参考音频加载为 16 kHz，并断言时长不超过 30 秒。这个限制属于参考音频，不是生成音频总时长限制。此次所读来源未提供统一最低时长和效果保证。[S4] |
| 音色复用 | `add_zero_shot_spk` 将参考条件保存在 `spk2info`，`save_spkinfo` 可落盘；后续可以通过 `zero_shot_spk_id` 复用。CosyVoice3 通过继承具备这些方法。业务仍需管理音色 ID、资产版本和账号绑定。[S5] |
| 输出采样率 | 官方模型仓库的 `cosyvoice3.yaml` 指定 `sample_rate: 24000`，运行代码读取配置中的采样率。参考音频内部按 16 kHz/24 kHz 提取不同特征，不意味着最终输出是 16 kHz。[S4][S5][S6] |
| 服务接口 | 官方包含 FastAPI 服务，提供 `/inference_zero_shot` 等接口，服务通过 `AutoModel` 选择模型；`AutoModel` 支持 CosyVoice3。当前示例流响应将音频转为原始 int16 PCM，没有 WAV 文件头；本项目应封装为明确格式的文件，并返回采样率、声道、时长等元信息。[S5][S7] |

## 2. 部署和硬件边界

官方安装流程使用 Conda、Python 3.10、仓库 requirements；说明提供 Ubuntu/CentOS 的 sox 安装命令，服务部署示例使用 Linux Docker 与 NVIDIA runtime。可选 `ttsfrd` 包的示例 wheel 是 `linux_x86_64`，不安装时默认使用 wetext。[S1]

这些资料能支持“优先评估 Linux 运行环境”的技术建议，但不能推出“官方保证原生 Windows 安装成功”或“Windows 完全无法运行”。建议本项目保留 Windows 浏览器操作与本机部署目标，TTS 服务优先验证 WSL2/Linux 容器路线；这是项目部署建议，不是已经完成的兼容性验证。本次会话另外检查到 Docker 29.8.0 服务正常、WSL2 的 docker-desktop 正在运行，尚未验证容器 GPU 透传。

CosyVoice3 构造函数默认 `fp16=False`，允许开启 FP16、TensorRT、vLLM；没有 CUDA 时会关闭 TensorRT/FP16。前端也存在 CPU 路径。代码中的 CPU 分支不代表 CPU 可满足本项目的合成耗时要求。另有 TensorRT DiT FP16 引擎性能问题警告，因此不能默认开启所有加速项。[S4][S5]

在本次检查的官方 README、模型卡和相关源码中，没有找到针对 RTX 3060 Laptop 的最低显存保证、每条 30～60 秒口播的可靠耗时保证或本机并发保证。[S1][S2][S5]

本次会话环境检查记录：GPU 名称报告为 NVIDIA RTX 3060 Laptop，显存总量报告为 12288 MiB、空闲约 4609 MiB，主机内存约 39.6 GiB。此项为当时机器状态，不是模型官方配置要求；显存报告与常见设备规格的差异也不应用来推定一定能运行。应以实际推理设备识别、峰值占用与连续任务结果为准。

首期建议单任务串行运行，先完成短句再完成 30～60 秒配音，记录加载时间、峰值显存、推理时间、实际音频长度和失败原因。若资源不足，先释放其他模型占用、调整分句与推理配置；是否切换云端配音应另作产品决定。

## 3. 许可证核验边界

官方代码仓库 `LICENSE` 为 Apache License 2.0。[S8]

本次已访问官方 ModelScope 模型卡，页面元数据的 License 字段为空；模型仓库 `LICENSE` 文件查询返回文件内容为空。Hugging Face 官方模型卡访问超时。因此，本次核验只确认代码仓库许可证，未独立确认所选模型权重的完整许可声明；不将代码许可自动等同于全部模型资产许可。[S2][S9][S10]

## 4. 建议写入 PRD 的范围

以下为针对本项目的产品建议，与上面的官方事实分开记录：

1. TTS 首期改用本地 CosyVoice3；文本、文生图等已确认的百炼调用方向继续按对应模块设计。
2. 支持上传参考音频、录入或校对对应文本、生成试听、保存音色、为账号选择默认音色。首期用 zero-shot，不增加模型训练流程。
3. 参考音频上传校验与所选模型版本保持一致，当前上限为 30 秒；清晰单人录音、减少背景音乐属于素材质量建议，实际效果通过试听确认。
4. 用户确认后的口播稿调用本地服务生成配音，保存音频与实际时长，再交给支持外部音频驱动的数字人服务。TTS 成功不等于嘴型同步链路已经验证成功。
5. 在联调验收中验证音色创建、重启后复用、配音可播放、任务失败可定位，以及 30～60 秒口播与视频的实际衔接。暂不承诺实时生成或固定音色相似度。
6. 本地 TTS 无按次云 API 调用费，但仍有硬件资源占用；既有 200 元 API 预算仅约束实际付费 API 调用。

## 5. 一手来源

以下链接均在 2026-09-21 核验，`main` / `master` 为可变化引用，正式部署应固定代码提交和模型版本。

- [S1：FunAudioLLM/CosyVoice 官方 README](https://github.com/FunAudioLLM/CosyVoice/blob/main/README.md)
- [S2：官方 ModelScope 模型卡](https://modelscope.cn/models/FunAudioLLM/Fun-CosyVoice3-0.5B-2512)
- [S3：官方 example.py，cosyvoice3_example](https://github.com/FunAudioLLM/CosyVoice/blob/main/example.py)
- [S4：官方 frontend.py，参考音频与文本处理](https://github.com/FunAudioLLM/CosyVoice/blob/main/cosyvoice/cli/frontend.py)
- [S5：官方 cosyvoice.py，模型加载、精度、音色复用与推理接口](https://github.com/FunAudioLLM/CosyVoice/blob/main/cosyvoice/cli/cosyvoice.py)
- [S6：官方模型仓库 cosyvoice3.yaml](https://modelscope.cn/api/v1/models/FunAudioLLM/Fun-CosyVoice3-0.5B-2512/repo?Revision=master&FilePath=cosyvoice3.yaml)
- [S7：官方 FastAPI 服务代码](https://github.com/FunAudioLLM/CosyVoice/blob/main/runtime/python/fastapi/server.py)
- [S8：官方代码仓库 LICENSE](https://github.com/FunAudioLLM/CosyVoice/blob/main/LICENSE)
- [S9：官方模型仓库 LICENSE 查询地址，本次返回文件内容为空](https://modelscope.cn/api/v1/models/FunAudioLLM/Fun-CosyVoice3-0.5B-2512/repo?Revision=master&FilePath=LICENSE)
- [S10：官方 Hugging Face 模型卡，本次访问超时，未作为已读事实依据](https://huggingface.co/FunAudioLLM/Fun-CosyVoice3-0.5B-2512)
