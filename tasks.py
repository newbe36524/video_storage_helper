from __future__ import annotations

from pathlib import Path

from invoke import Collection, task

from video_storage_helper import load_config, main as app_main, select_encoder


@task(help={"config": "Path to config.yml"})
def run(c, config="config.yml"):
    raise SystemExit(app_main(["--config", config]))


@task(help={"config": "Path to config.yml"})
def check(c, config="config.yml"):
    config_path = Path(config).expanduser().resolve()
    loaded_config = load_config(config_path)
    encoder = select_encoder(loaded_config)
    print(f"config: {config_path}")
    print(f"input_dirs: {', '.join(str(path) for path in loaded_config.input_dirs)}")
    print(f"output_dir: {loaded_config.output_dir}")
    print(f"data_file: {loaded_config.data_file}")
    print(f"encoder: {encoder}")


ns = Collection(run, check)