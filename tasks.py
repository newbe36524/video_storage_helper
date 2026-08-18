from __future__ import annotations

from pathlib import Path
import shutil

from invoke import Collection, task

from video_storage_helper import load_config, main as app_main, select_encoder


@task(help={"config": "Path to config.yml", "operation": "transcode or archive_7z"})
def run(c, config="config.yml", operation="transcode"):
    raise SystemExit(app_main(["--config", config, "--operation", operation]))


@task(help={"config": "Path to config.yml", "operation": "transcode or archive_7z"})
def check(c, config="config.yml", operation="transcode"):
    config_path = Path(config).expanduser().resolve()
    loaded_config = load_config(config_path, operation=operation)
    processor = "7z" if loaded_config.operation == "archive_7z" else select_encoder(loaded_config)
    seven_zip_path = Path(loaded_config.seven_zip).expanduser()
    if loaded_config.operation == "archive_7z" and shutil.which(loaded_config.seven_zip) is None and not seven_zip_path.exists():
        raise RuntimeError(f"7z executable not found: {loaded_config.seven_zip}")
    print(f"config: {config_path}")
    print(f"input_dirs: {', '.join(str(path) for path in loaded_config.input_dirs)}")
    print(f"output_dir: {loaded_config.output_dir}")
    print(f"data_file: {loaded_config.data_file}")
    print(f"operation: {loaded_config.operation}")
    print(f"processor: {processor}")


ns = Collection(run, check)