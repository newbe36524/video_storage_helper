#!/usr/bin/env python3

from __future__ import annotations

import argparse
import concurrent.futures
import dataclasses
import datetime as dt
import hashlib
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml


DEFAULT_EXTENSIONS = [
    ".mp4",
    ".mkv",
    ".mov",
    ".avi",
    ".webm",
    ".m4v",
    ".ts",
    ".mts",
    ".m2ts",
]


@dataclasses.dataclass(frozen=True)
class AppConfig:
    input_dirs: list[Path]
    output_dir: Path
    data_file: Path
    extensions: set[str]
    ffmpeg: str
    ffprobe: str
    hwaccel: str
    cuda_decoder: str
    concurrent_workers: int
    encoder_preference: str
    nvenc_preset: str
    nvenc_tune: str
    nvenc_cq: int
    nvenc_spatial_aq: bool
    nvenc_temporal_aq: bool
    nvenc_multipass: str
    nvenc_rc_lookahead: int
    svtav1_preset: int
    svtav1_crf: int
    audio_codec: str
    audio_bitrate: str
    faststart: bool
    overwrite: bool


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle)
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise ValueError(f"YAML file must contain a mapping: {path}")
    return loaded


def dump_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(data, handle, allow_unicode=True, sort_keys=True)


def resolve_path(base_dir: Path, raw_value: str | os.PathLike[str]) -> Path:
    value = Path(raw_value)
    return value if value.is_absolute() else (base_dir / value).resolve()


def parse_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def parse_extensions(values: Any) -> set[str]:
    if not values:
        values = DEFAULT_EXTENSIONS
    return {str(item).lower() for item in values}


def load_config(config_path: Path) -> AppConfig:
    raw = load_yaml(config_path)
    base_dir = config_path.parent.resolve()

    input_dirs = raw.get("input_dirs") or raw.get("input_folders") or []
    if not input_dirs:
        raise ValueError("config.yml must define at least one input dir under input_dirs")

    resolved_input_dirs = [resolve_path(base_dir, item) for item in input_dirs]
    output_dir = resolve_path(base_dir, raw.get("output_dir", "output"))
    data_file = resolve_path(base_dir, raw.get("data_file", "data.yml"))

    return AppConfig(
        input_dirs=resolved_input_dirs,
        output_dir=output_dir,
        data_file=data_file,
        extensions=parse_extensions(raw.get("extensions", DEFAULT_EXTENSIONS)),
        ffmpeg=str(raw.get("ffmpeg", "ffmpeg")),
        ffprobe=str(raw.get("ffprobe", "ffprobe")),
        hwaccel=str(raw.get("hwaccel", "cuda")),
        cuda_decoder=str(raw.get("cuda_decoder", "auto")),
        concurrent_workers=max(1, int(raw.get("concurrent_workers", 5))),
        encoder_preference=str(raw.get("encoder_preference", "auto")).lower(),
        nvenc_preset=str(raw.get("nvenc_preset", "p5")),
        nvenc_tune=str(raw.get("nvenc_tune", "hq")),
        nvenc_cq=int(raw.get("nvenc_cq", 28)),
        nvenc_spatial_aq=parse_bool(raw.get("nvenc_spatial_aq", True), True),
        nvenc_temporal_aq=parse_bool(raw.get("nvenc_temporal_aq", True), True),
        nvenc_multipass=str(raw.get("nvenc_multipass", "disabled")),
        nvenc_rc_lookahead=int(raw.get("nvenc_rc_lookahead", 0)),
        svtav1_preset=int(raw.get("svtav1_preset", 6)),
        svtav1_crf=int(raw.get("svtav1_crf", 32)),
        audio_codec=str(raw.get("audio_codec", "aac")),
        audio_bitrate=str(raw.get("audio_bitrate", "192k")),
        faststart=parse_bool(raw.get("faststart", True), True),
        overwrite=parse_bool(raw.get("overwrite", False), False),
    )


def sha256_of_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def is_within(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def discover_video_files(input_dirs: list[Path], extensions: set[str], excluded_dirs: list[Path]) -> list[Path]:
    discovered: list[Path] = []
    excluded = [path.resolve() for path in excluded_dirs]

    for input_dir in input_dirs:
        if not input_dir.exists():
            print(f"skip missing input dir: {input_dir}", file=sys.stderr)
            continue
        for root, dirs, files in os.walk(input_dir):
            root_path = Path(root)
            dirs[:] = [name for name in dirs if not any(is_within(root_path / name, excluded_path) for excluded_path in excluded)]
            if any(is_within(root_path, excluded_path) for excluded_path in excluded):
                continue
            for filename in files:
                candidate = root_path / filename
                if candidate.suffix.lower() in extensions:
                    discovered.append(candidate)

    return discovered


def load_history(data_file: Path) -> dict[str, Any]:
    if not data_file.exists():
        return {"version": 1, "history": {}}
    loaded = load_yaml(data_file)
    if not loaded:
        return {"version": 1, "history": {}}
    if "history" not in loaded or not isinstance(loaded["history"], dict):
        loaded["history"] = {}
    loaded.setdefault("version", 1)
    return loaded


def check_ffmpeg_encoders(ffmpeg_executable: str) -> set[str]:
    result = subprocess.run(
        [ffmpeg_executable, "-hide_banner", "-encoders"],
        capture_output=True,
        text=True,
        check=True,
    )
    encoders: set[str] = set()
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].startswith("V"):
            encoders.add(parts[1])
    return encoders


def check_ffmpeg_decoders(ffmpeg_executable: str) -> set[str]:
    result = subprocess.run(
        [ffmpeg_executable, "-hide_banner", "-decoders"],
        capture_output=True,
        text=True,
        check=True,
    )
    decoders: set[str] = set()
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].startswith("V"):
            decoders.add(parts[1])
    return decoders


def probe_video_codec(config: AppConfig, source: Path) -> str | None:
    result = subprocess.run(
        [
            config.ffprobe,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(source),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    codec_name = result.stdout.strip().lower()
    return codec_name or None


def select_cuda_decoder(codec_name: str | None) -> str | None:
    if codec_name in {"h264", "avc1", "mpeg4"}:
        return "h264_cuvid"
    if codec_name in {"hevc", "h265", "hvc1"}:
        return "hevc_cuvid"
    if codec_name in {"mpeg2video"}:
        return "mpeg2_cuvid"
    if codec_name in {"vp9"}:
        return "vp9_cuvid"
    return None


def resolve_cuda_decoder(config: AppConfig, codec_name: str | None) -> str | None:
    available_decoders = check_ffmpeg_decoders(config.ffmpeg)
    return resolve_cuda_decoder_from_set(config, codec_name, available_decoders)


def resolve_cuda_decoder_from_set(config: AppConfig, codec_name: str | None, available_decoders: set[str]) -> str | None:
    if config.cuda_decoder != "auto":
        if config.cuda_decoder in available_decoders:
            return config.cuda_decoder
        print(
            f"warn: configured cuda_decoder '{config.cuda_decoder}' is unavailable, fallback to software decode",
            file=sys.stderr,
        )
        return None

    auto_decoder = select_cuda_decoder(codec_name)
    if auto_decoder and auto_decoder in available_decoders:
        return auto_decoder
    return None


def build_decode_input_args(
    config: AppConfig,
    source: Path,
    encoder: str,
    available_decoders: set[str],
) -> tuple[list[str], str]:
    if encoder != "av1_nvenc" or config.hwaccel != "cuda":
        return [], "software"

    codec_name = probe_video_codec(config, source)
    cuda_decoder = resolve_cuda_decoder_from_set(config, codec_name, available_decoders)
    if cuda_decoder is None:
        return [], "software"

    return ["-hwaccel", "cuda", "-hwaccel_output_format", "cuda", "-c:v", cuda_decoder], f"cuda+{cuda_decoder}"


def print_detection(config: AppConfig, encoder: str, available_encoders: set[str], available_decoders: set[str]) -> None:
    av1_encoders = sorted([name for name in available_encoders if "av1" in name])
    cuvid_decoders = sorted([name for name in available_decoders if name.endswith("_cuvid")])

    print("detection:")
    print(f"  selected_encoder: {encoder}")
    print(f"  hwaccel_config: {config.hwaccel}")
    print(f"  cuda_decoder_config: {config.cuda_decoder}")
    print(f"  concurrent_workers: {config.concurrent_workers}")
    print(f"  available_av1_encoders: {', '.join(av1_encoders) if av1_encoders else 'none'}")
    print(f"  available_cuvid_decoders: {', '.join(cuvid_decoders) if cuvid_decoders else 'none'}")
    if config.hwaccel == "cuda" and encoder == "av1_nvenc":
        if cuvid_decoders:
            print("  decode_strategy: try cuda decoder per input codec, fallback to software")
        else:
            print("  decode_strategy: software (no cuvid decoder available)")
    else:
        print("  decode_strategy: software")


def select_encoder(config: AppConfig) -> str:
    available = check_ffmpeg_encoders(config.ffmpeg)
    preference = config.encoder_preference

    if preference == "nvidia":
        if "av1_nvenc" in available:
            return "av1_nvenc"
        raise RuntimeError("encoder_preference=nvidia but av1_nvenc is not available in ffmpeg")

    if preference == "software":
        for encoder in ("libsvtav1", "libaom-av1"):
            if encoder in available:
                return encoder
        raise RuntimeError("encoder_preference=software but no AV1 software encoder is available")

    for encoder in ("av1_nvenc", "libsvtav1", "libaom-av1"):
        if encoder in available:
            return encoder

    raise RuntimeError("No AV1 encoder was found in ffmpeg")


def build_output_path(config: AppConfig, source: Path, sha256_value: str) -> Path:
    safe_stem = source.stem.replace(" ", "_")
    return config.output_dir / f"{safe_stem}__{sha256_value[:12]}.mp4"


def build_ffmpeg_command(
    config: AppConfig,
    source: Path,
    output: Path,
    encoder: str,
    available_decoders: set[str],
) -> tuple[list[str], str]:
    command = [config.ffmpeg, "-hide_banner", "-y" if config.overwrite else "-n"]

    decode_args, decode_label = build_decode_input_args(config, source, encoder, available_decoders)
    command.extend(decode_args)

    command.extend(["-i", str(source)])

    if encoder == "av1_nvenc":
        command.extend(
            [
                "-map",
                "0:v:0",
                "-map",
                "0:a?",
                "-map_metadata",
                "0",
                "-c:v",
                encoder,
                "-preset",
                config.nvenc_preset,
                "-tune",
                config.nvenc_tune,
                "-rc",
                "vbr",
                "-multipass",
                config.nvenc_multipass,
                "-rc-lookahead",
                str(config.nvenc_rc_lookahead),
                "-cq",
                str(config.nvenc_cq),
                "-b:v",
                "0",
                "-pix_fmt",
                "yuv420p",
            ]
        )
        if config.nvenc_spatial_aq:
            command.extend(["-spatial-aq", "1"])
        if config.nvenc_temporal_aq:
            command.extend(["-temporal-aq", "1"])
    elif encoder == "libsvtav1":
        command.extend(
            [
                "-map",
                "0:v:0",
                "-map",
                "0:a?",
                "-map_metadata",
                "0",
                "-c:v",
                encoder,
                "-preset",
                str(config.svtav1_preset),
                "-crf",
                str(config.svtav1_crf),
                "-pix_fmt",
                "yuv420p",
            ]
        )
    else:
        command.extend(
            [
                "-map",
                "0:v:0",
                "-map",
                "0:a?",
                "-map_metadata",
                "0",
                "-c:v",
                encoder,
                "-crf",
                "32",
                "-b:v",
                "0",
                "-pix_fmt",
                "yuv420p",
            ]
        )

    command.extend(
        [
            "-c:a",
            config.audio_codec,
            "-b:a",
            config.audio_bitrate,
            "-sn",
            "-dn",
        ]
    )

    if config.faststart:
        command.extend(["-movflags", "+faststart"])

    command.append(str(output))
    return command, decode_label


def process_file(
    config: AppConfig,
    source: Path,
    sha256_value: str,
    encoder: str,
    available_decoders: set[str],
    output_path: Path,
) -> tuple[str, str, str, str]:

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_output = output_path.with_suffix(".part.mp4")
    if temp_output.exists():
        temp_output.unlink()

    command, decode_label = build_ffmpeg_command(config, source, temp_output, encoder, available_decoders)
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        if temp_output.exists():
            temp_output.unlink()
        raise RuntimeError(
            f"ffmpeg failed for {source}\nCMD: {' '.join(command)}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )

    temp_output.replace(output_path)
    return sha256_value, str(source), str(output_path), decode_label


def save_history(data_file: Path, history: dict[str, Any]) -> None:
    dump_yaml(data_file, history)


def print_plan(config: AppConfig, encoder: str, candidate_count: int) -> None:
    print("plan:")
    print(f"  1. scan input_dirs: {', '.join(str(path) for path in config.input_dirs)}")
    print(f"  2. detect new video files by sha256 across {candidate_count} candidate(s)")
    print(f"  3. encode to mp4 with {encoder} into {config.output_dir}")


def print_summary(
    processed: int,
    skipped: int,
    failed: int,
    encoder: str,
    output_dir: Path,
    data_file: Path,
    status: str = "ok",
) -> None:
    print("summary:")
    print(f"  status: {status}")
    print(f"  processed: {processed}")
    print(f"  skipped: {skipped}")
    print(f"  failed: {failed}")
    print(f"  encoder: {encoder}")
    print(f"  output_dir: {output_dir}")
    print(f"  data_file: {data_file}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scan video folders and transcode to AV1 mp4.")
    parser.add_argument("--config", default="config.yml", help="Path to config.yml")
    args = parser.parse_args(argv)

    config_path = Path(args.config).expanduser().resolve()
    if not config_path.exists():
        print(f"config file not found: {config_path}", file=sys.stderr)
        print("summary:")
        print("  status: failed")
        print(f"  config: {config_path}")
        return 2

    summary_ready = False
    summary_processed = 0
    summary_skipped = 0
    summary_failed = 0
    summary_encoder = "unknown"
    summary_output_dir = Path(".")
    summary_data_file = Path("data.yml")
    summary_printed = False

    try:
        config = load_config(config_path)
        available_encoders = check_ffmpeg_encoders(config.ffmpeg)
        available_decoders = check_ffmpeg_decoders(config.ffmpeg)
        encoder = select_encoder(config)
        history = load_history(config.data_file)
        candidates = discover_video_files(config.input_dirs, config.extensions, [config.output_dir])

        summary_ready = True
        summary_encoder = encoder
        summary_output_dir = config.output_dir
        summary_data_file = config.data_file

        processed = 0
        skipped = 0
        failed = 0
        history_map = history.setdefault("history", {})
        queued_sha256: set[str] = set()
        submitted_jobs = 0

        print_detection(config, encoder, available_encoders, available_decoders)
        print_plan(config, encoder, len(candidates))
        print("process:")
        if not candidates:
            print("  none")

        with concurrent.futures.ThreadPoolExecutor(max_workers=config.concurrent_workers) as executor:
            future_to_job: dict[concurrent.futures.Future[tuple[str, str, str, str]], tuple[Path, str, Path]] = {}

            for source in sorted(candidates):
                print(f"process: {source}")
                try:
                    sha256_value = sha256_of_file(source)
                    record = history_map.setdefault(sha256_value, {})
                    output_path = Path(record.get("output_path") or build_output_path(config, source, sha256_value))

                    source_stat = source.stat()
                    sources = record.setdefault("sources", [])
                    sources.append(
                        {
                            "path": str(source),
                            "size": source_stat.st_size,
                            "mtime": source_stat.st_mtime,
                            "seen_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                        }
                    )

                    already_processed = bool(record.get("processed_at")) and output_path.exists()
                    if already_processed:
                        record.setdefault("output_path", str(output_path))
                        record.setdefault("encoder", encoder)
                        print(f"skip processed {source}")
                        skipped += 1
                        continue

                    if sha256_value in queued_sha256:
                        print(f"skip duplicate sha256 in this batch: {source}")
                        skipped += 1
                        continue

                    queued_sha256.add(sha256_value)
                    future = executor.submit(
                        process_file,
                        config,
                        source,
                        sha256_value,
                        encoder,
                        available_decoders,
                        output_path,
                    )
                    future_to_job[future] = (source, sha256_value, output_path)
                    submitted_jobs += 1
                except Exception as exc:  # pragma: no cover - surfaced to the user
                    failed += 1
                    print(f"error: {source}: {exc}", file=sys.stderr)

            if submitted_jobs:
                print(f"parallel: running {submitted_jobs} job(s) with workers={config.concurrent_workers}")

            for future in concurrent.futures.as_completed(future_to_job):
                source, sha256_value, output_path = future_to_job[future]
                try:
                    done_sha, done_source, done_output_path, decode_label = future.result()
                    record = history_map.setdefault(done_sha, {})
                    record.update(
                        {
                            "output_path": done_output_path,
                            "encoder": encoder,
                            "decode": decode_label,
                            "processed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                            "status": "processed",
                        }
                    )
                    print(f"processed {done_source} -> {done_output_path} (decode={decode_label})")
                    processed += 1
                except Exception as exc:  # pragma: no cover - surfaced to the user
                    record = history_map.setdefault(sha256_value, {})
                    record.update(
                        {
                            "output_path": str(output_path),
                            "encoder": encoder,
                            "status": "failed",
                            "last_error": str(exc),
                            "failed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                        }
                    )
                    failed += 1
                    print(f"error: {source}: {exc}", file=sys.stderr)
                finally:
                    save_history(config.data_file, history)

        summary_processed = processed
        summary_skipped = skipped
        summary_failed = failed
        print_summary(processed, skipped, failed, encoder, config.output_dir, config.data_file)
        summary_printed = True
        return 0 if failed == 0 else 1
    except Exception as exc:
        print(f"fatal: {exc}", file=sys.stderr)
        summary_failed = max(summary_failed, 1)
        return 1
    finally:
        if summary_ready and not summary_printed:
            print_summary(
                summary_processed,
                summary_skipped,
                summary_failed,
                summary_encoder,
                summary_output_dir,
                summary_data_file,
                status="failed",
            )
        elif not summary_ready:
            print_summary(
                summary_processed,
                summary_skipped,
                summary_failed,
                summary_encoder,
                summary_output_dir,
                summary_data_file,
                status="failed",
            )


if __name__ == "__main__":
    raise SystemExit(main())