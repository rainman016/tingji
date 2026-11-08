# 第三方来源

本项目的源码许可不替代以下软件、模型或构建产物的许可。依赖和权重由安装器从上游下载，仓库不重新打包它们。使用和分发时请核对具体版本的许可证、模型卡及随附说明。

| 用途 | 上游 |
| --- | --- |
| 桌面组件 | [CustomTkinter](https://github.com/TomSchimansky/CustomTkinter) |
| 中文识别 | [FunASR](https://github.com/modelscope/FunASR)、[Paraformer](https://huggingface.co/funasr/paraformer-zh) |
| 人声、标点、声纹 | [FSMN-VAD](https://huggingface.co/funasr/fsmn-vad)、[CT-Punc](https://huggingface.co/funasr/ct-punc)、[CAM++](https://huggingface.co/funasr/campplus) |
| 英文识别与语言判断 | [faster-whisper](https://github.com/SYSTRAN/faster-whisper)、[Whisper small.en](https://huggingface.co/Systran/faster-whisper-small.en)、[Whisper tiny](https://huggingface.co/Systran/faster-whisper-tiny) |
| 摘要模型 | [Qwen3-4B-GGUF](https://huggingface.co/Qwen/Qwen3-4B-GGUF) |
| 摘要引擎 | [llama-cpp-python](https://github.com/abetlen/llama-cpp-python)、[llama.cpp](https://github.com/ggml-org/llama.cpp) |
| 音视频解码 | [imageio-ffmpeg](https://github.com/imageio/imageio-ffmpeg)、[FFmpeg licensing](https://ffmpeg.org/legal.html) |
| 张量运行库 | [PyTorch](https://github.com/pytorch/pytorch) |

FFmpeg 许可取决于实际构建配置，可运行该构建的 `-L` 参数查看；源码仓库中不附带 FFmpeg 二进制。图标和公开演示截图由本项目生成，演示内容为合成示例。
