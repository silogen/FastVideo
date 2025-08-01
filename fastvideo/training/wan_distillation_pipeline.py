# SPDX-License-Identifier: Apache-2.0
import sys
from copy import deepcopy

from fastvideo.fastvideo_args import FastVideoArgs, TrainingArgs
from fastvideo.logger import init_logger
from fastvideo.models.schedulers.scheduling_flow_match_euler_discrete import (
    FlowMatchEulerDiscreteScheduler)
from fastvideo.pipelines.basic.wan.wan_dmd_pipeline import WanDMDPipeline
from fastvideo.training.distillation_pipeline import DistillationPipeline
from fastvideo.utils import is_vsa_available

vsa_available = is_vsa_available()

logger = init_logger(__name__)


class WanDistillationPipeline(DistillationPipeline):
    """
    A distillation pipeline for Wan that uses a single transformer model.
    The main transformer serves as the student model, and copies are made for teacher and critic.
    """
    _required_config_modules = [
        "scheduler", "transformer", "vae", "real_score_transformer",
        "fake_score_transformer"
    ]

    def initialize_pipeline(self, fastvideo_args: FastVideoArgs):
        """Initialize Wan-specific scheduler."""
        self.modules["scheduler"] = FlowMatchEulerDiscreteScheduler(
            shift=fastvideo_args.pipeline_config.flow_shift)

    def create_training_stages(self, training_args: TrainingArgs):
        """
        May be used in future refactors.
        """
        pass

    def initialize_validation_pipeline(self, training_args: TrainingArgs):
        logger.info("Initializing validation pipeline...")
        args_copy = deepcopy(training_args)

        args_copy.inference_mode = True
        args_copy.dit_cpu_offload = True
        args_copy.pipeline_config.vae_config.load_encoder = False
        validation_pipeline = WanDMDPipeline.from_pretrained(
            training_args.model_path,
            args=args_copy,  # type: ignore
            inference_mode=True,
            loaded_modules={"transformer": self.get_module("transformer")},
            tp_size=training_args.tp_size,
            sp_size=training_args.sp_size,
            num_gpus=training_args.num_gpus)

        self.validation_pipeline = validation_pipeline


def main(args) -> None:
    logger.info("Starting Wan distillation pipeline...")

    # Create pipeline with original args
    pipeline = WanDistillationPipeline.from_pretrained(
        args.pretrained_model_name_or_path, args=args)

    args = pipeline.training_args
    # Start training
    pipeline.train()
    logger.info("Wan distillation pipeline completed")


if __name__ == "__main__":
    argv = sys.argv
    from fastvideo.fastvideo_args import TrainingArgs
    from fastvideo.utils import FlexibleArgumentParser
    parser = FlexibleArgumentParser()
    parser = TrainingArgs.add_cli_args(parser)
    parser = FastVideoArgs.add_cli_args(parser)
    args = parser.parse_args()
    main(args)
