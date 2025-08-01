import dataclasses
from typing import Any, Optional

from fastvideo.configs.utils import update_config_from_args
from fastvideo.utils import FlexibleArgumentParser, StoreBoolean


@dataclasses.dataclass
class PreprocessConfig:
    """Configuration for preprocessing operations."""

    # Model and dataset configuration
    model_path: str = ""
    dataset_path: str = ""
    dataset_output_dir: str = "./output"

    # Dataloader configuration
    dataloader_num_workers: int = 1
    preprocess_video_batch_size: int = 2

    # Saver configuration
    samples_per_file: int = 64
    flush_frequency: int = 256

    # Video processing parameters
    max_height: int = 480
    max_width: int = 848
    num_frames: int = 163
    video_length_tolerance_range: float = 2.0
    train_fps: int = 30
    speed_factor: float = 1.0
    drop_short_ratio: float = 1.0
    do_temporal_sample: bool = False

    # Model configuration
    training_cfg_rate: float = 0.0

    @staticmethod
    def add_cli_args(parser: FlexibleArgumentParser,
                     prefix: str = "preprocess") -> FlexibleArgumentParser:
        """Add preprocessing configuration arguments to the parser."""
        prefix_with_dot = f"{prefix}." if (prefix.strip() != "") else ""

        preprocess_args = parser.add_argument_group("Preprocessing Arguments")
        # Model & Dataset
        preprocess_args.add_argument(f"--{prefix_with_dot}model-path",
                                     type=str,
                                     default=PreprocessConfig.model_path,
                                     help="Path to the model for preprocessing")
        preprocess_args.add_argument(
            f"--{prefix_with_dot}dataset-path",
            type=str,
            default=PreprocessConfig.dataset_path,
            help="Path to the dataset directory for preprocessing")
        preprocess_args.add_argument(
            f"--{prefix_with_dot}dataset-output-dir",
            type=str,
            default=PreprocessConfig.dataset_output_dir,
            help="The output directory where the dataset will be written.")

        # Dataloader
        preprocess_args.add_argument(
            f"--{prefix_with_dot}dataloader-num-workers",
            type=int,
            default=PreprocessConfig.dataloader_num_workers,
            help=
            "Number of subprocesses to use for data loading. 0 means that the data will be loaded in the main process."
        )
        preprocess_args.add_argument(
            f"--{prefix_with_dot}preprocess-video-batch-size",
            type=int,
            default=PreprocessConfig.preprocess_video_batch_size,
            help="Batch size (per device) for the training dataloader.")

        # Saver
        preprocess_args.add_argument(f"--{prefix_with_dot}samples-per-file",
                                     type=int,
                                     default=PreprocessConfig.samples_per_file,
                                     help="Number of samples per output file")
        preprocess_args.add_argument(f"--{prefix_with_dot}flush-frequency",
                                     type=int,
                                     default=PreprocessConfig.flush_frequency,
                                     help="How often to save to parquet files")

        # Video processing parameters
        preprocess_args.add_argument(f"--{prefix_with_dot}max-height",
                                     type=int,
                                     default=PreprocessConfig.max_height,
                                     help="Maximum height for video processing")
        preprocess_args.add_argument(f"--{prefix_with_dot}max-width",
                                     type=int,
                                     default=PreprocessConfig.max_width,
                                     help="Maximum width for video processing")
        preprocess_args.add_argument(f"--{prefix_with_dot}num-frames",
                                     type=int,
                                     default=PreprocessConfig.num_frames,
                                     help="Number of frames to process")
        preprocess_args.add_argument(
            f"--{prefix_with_dot}video-length-tolerance-range",
            type=float,
            default=PreprocessConfig.video_length_tolerance_range,
            help="Video length tolerance range")
        preprocess_args.add_argument(f"--{prefix_with_dot}train-fps",
                                     type=int,
                                     default=PreprocessConfig.train_fps,
                                     help="Training FPS")
        preprocess_args.add_argument(f"--{prefix_with_dot}speed-factor",
                                     type=float,
                                     default=PreprocessConfig.speed_factor,
                                     help="Speed factor for video processing")
        preprocess_args.add_argument(f"--{prefix_with_dot}drop-short-ratio",
                                     type=float,
                                     default=PreprocessConfig.drop_short_ratio,
                                     help="Ratio for dropping short videos")
        preprocess_args.add_argument(
            f"--{prefix_with_dot}do-temporal-sample",
            action=StoreBoolean,
            default=PreprocessConfig.do_temporal_sample,
            help="Whether to do temporal sampling")

        # Model Training configuration
        preprocess_args.add_argument(f"--{prefix_with_dot}training-cfg-rate",
                                     type=float,
                                     default=PreprocessConfig.training_cfg_rate,
                                     help="Training CFG rate")

        return parser

    @classmethod
    def from_kwargs(cls, kwargs: dict[str,
                                      Any]) -> Optional["PreprocessConfig"]:
        """Create PreprocessConfig from keyword arguments."""
        preprocess_config = cls()
        if not update_config_from_args(
                preprocess_config, kwargs, prefix="preprocess", pop_args=True):
            return None
        return preprocess_config

    def check_preprocess_config(self) -> None:
        if self.dataset_path == "":
            raise ValueError("dataset_path must be set for preprocess mode")
        if self.samples_per_file <= 0:
            raise ValueError("samples_per_file must be greater than 0")
        if self.flush_frequency <= 0:
            raise ValueError("flush_frequency must be greater than 0")
