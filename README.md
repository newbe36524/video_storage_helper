# video_storage_helper

批量扫描多个输入目录中的文件，按 sha256 去重后执行视频转码或 7z 加密。视频模式优先使用 NVIDIA 的 `av1_nvenc` 将视频转成 AV1 并封装为 mp4；如果本机 ffmpeg 没有可用的 NVIDIA AV1 编码器，则自动回退到软件编码器。

## 文件

- `video_storage_helper.py`：主脚本
- `tasks.py`：`invoke` 任务入口
- `run.sh`：一键启动脚本，会自动创建 `.venv`
- `config.example.yml`：配置示例
- `requirements.txt`：Python 依赖

## 用法

1. 复制 `config.example.yml` 为 `config.yml`，分别修改 `transcode_input_dirs`、`transcode_output_dir`、`zip_input_dirs` 和 `zip_output_dir`。
2. 执行视频转码：`./transcode.sh`
3. 执行 7z 加密：先设置 `VIDEO_STORAGE_7Z_PASSWORD`，再执行 `./zip.sh`
4. `run.sh` 保留为转码入口的兼容别名。

## 配置要点

- `input_dirs` 支持多个输入目录。
- `transcode_input_dirs` 和 `transcode_output_dir` 只用于视频转码。
- `zip_input_dirs` 和 `zip_output_dir` 只用于 7z 加密。
- `extensions` 决定扫描的文件类型；7z 模式也使用这个配置，若要加密普通文件需要加入对应扩展名。
- `data_file` 保存历史记录，脚本会在首次运行时自动创建。
- `encoder_preference` 默认为 `auto`，会优先尝试 `av1_nvenc`。
- `audio_codec` 默认是 `aac`，便于输出 mp4 的兼容性。
- `concurrent_workers` 控制并发转码数量，默认 `5`。
- `hash_prefix_bytes` 控制去重时只取文件前多少字节做 SHA256，默认 `1048576`（1 MiB）。
- `max_processed_per_run` 控制本次运行最多成功处理多少个新文件，默认 `0` 表示不限制；跳过的文件不计入这个上限。
- 7z 模式使用 `seven_zip` 指定命令，密码默认从环境变量 `VIDEO_STORAGE_7Z_PASSWORD` 读取，也可以用 `seven_zip_password` 配置；密码不会写入历史记录。
- 转码和 7z 使用同一套扫描、sha256 去重、并发、输出和历史记录流程，两种操作的历史键彼此隔离。
- `hwaccel: cuda` 可以把可支持的输入改成 GPU 硬件解码；如果你的输入不是 H.264/H.265 或硬解不可用，脚本会继续走软件解码。
- `nvenc_preset: p1`、`nvenc_tune: ll`、关闭 AQ 和 lookahead，会明显提高吞吐，但画质与压缩效率会下降。

## Invoke 任务

- `invoke run --config config.yml --operation transcode`：执行视频转码
- `invoke run --config config.yml --operation archive_7z`：执行 7z 加密
- `invoke check --config config.yml`：检查配置和可用编码器，不会开始转码