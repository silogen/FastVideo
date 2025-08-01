# SPDX-License-Identifier: Apache-2.0
"""Registry for pipeline weight-specific configurations."""

import os
from collections.abc import Callable

from fastvideo.configs.pipelines.base import PipelineConfig
from fastvideo.configs.pipelines.hunyuan import FastHunyuanConfig, HunyuanConfig
from fastvideo.configs.pipelines.stepvideo import StepVideoT2VConfig
from fastvideo.configs.pipelines.wan import (FastWanT2V480PConfig,
                                             WanI2V480PConfig, WanI2V720PConfig,
                                             WanT2V480PConfig, WanT2V720PConfig)
from fastvideo.logger import init_logger
from fastvideo.utils import (maybe_download_model_index,
                             verify_model_config_and_directory)

logger = init_logger(__name__)

# Registry maps specific model weights to their config classes
PIPE_NAME_TO_CONFIG: dict[str, type[PipelineConfig]] = {
    "FastVideo/FastHunyuan-diffusers": FastHunyuanConfig,
    "hunyuanvideo-community/HunyuanVideo": HunyuanConfig,
    "Wan-AI/Wan2.1-T2V-1.3B-Diffusers": WanT2V480PConfig,
    "weizhou03/Wan2.1-Fun-1.3B-InP-Diffusers": WanI2V480PConfig,
    "Wan-AI/Wan2.1-I2V-14B-480P-Diffusers": WanI2V480PConfig,
    "Wan-AI/Wan2.1-I2V-14B-720P-Diffusers": WanI2V720PConfig,
    "Wan-AI/Wan2.1-T2V-14B-Diffusers": WanT2V720PConfig,
    "FastVideo/FastWan2.1-T2V-1.3B-Diffusers": FastWanT2V480PConfig,
    "FastVideo/stepvideo-t2v-diffusers": StepVideoT2VConfig,
    "FastVideo/Wan2.1-VSA-T2V-14B-720P-Diffusers": WanT2V720PConfig,
    "Wan-AI/Wan2.2-TI2V-5B-Diffusers": WanT2V720PConfig
    # Add other specific weight variants
}

# For determining pipeline type from model ID
PIPELINE_DETECTOR: dict[str, Callable[[str], bool]] = {
    "hunyuan": lambda id: "hunyuan" in id.lower(),
    "wanpipeline": lambda id: "wanpipeline" in id.lower(),
    "wanimagetovideo": lambda id: "wanimagetovideo" in id.lower(),
    "wandmdpipeline": lambda id: "wandmdpipeline" in id.lower(),
    "stepvideo": lambda id: "stepvideo" in id.lower(),
    # Add other pipeline architecture detectors
}

# Fallback configs when exact match isn't found but architecture is detected
PIPELINE_FALLBACK_CONFIG: dict[str, type[PipelineConfig]] = {
    "hunyuan":
    HunyuanConfig,  # Base Hunyuan config as fallback for any Hunyuan variant
    "wanpipeline":
    WanT2V480PConfig,  # Base Wan config as fallback for any Wan variant
    "wanimagetovideo": WanI2V480PConfig,
    "wandmdpipeline": FastWanT2V480PConfig,
    "stepvideo": StepVideoT2VConfig
    # Other fallbacks by architecture
}


def get_pipeline_config_cls_from_name(
        pipeline_name_or_path: str) -> type[PipelineConfig]:
    """Get the appropriate configuration class for a given pipeline name or path.

    This function implements a multi-step lookup process to find the most suitable
    configuration class for a given pipeline. It follows this order:
    1. Exact match in the PIPE_NAME_TO_CONFIG
    2. Partial match in the PIPE_NAME_TO_CONFIG
    3. Fallback to class name in the model_index.json
    4. else raise an error

    Args:
        pipeline_name_or_path (str): The name or path of the pipeline. This can be:
            - A registered model ID (e.g., "FastVideo/FastHunyuan-diffusers")
            - A local path to a model directory
            - A model ID that will be downloaded

    Returns:
        Type[PipelineConfig]: The configuration class that best matches the pipeline.
            This will be one of:
            - A specific weight configuration class if an exact match is found
            - A fallback configuration class based on the pipeline architecture
            - The base PipelineConfig class if no matches are found

    Note:
        - For local paths, the function will verify the model configuration
        - For remote models, it will attempt to download the model index
        - Warning messages are logged when falling back to less specific configurations
    """

    pipeline_config_cls: type[PipelineConfig] | None = None

    # First try exact match for specific weights
    if pipeline_name_or_path in PIPE_NAME_TO_CONFIG:
        pipeline_config_cls = PIPE_NAME_TO_CONFIG[pipeline_name_or_path]

    # Try partial matches (for local paths that might include the weight ID)
    for registered_id, config_class in PIPE_NAME_TO_CONFIG.items():
        if registered_id in pipeline_name_or_path:
            pipeline_config_cls = config_class
            break

    # If no match, try to use the fallback config
    if pipeline_config_cls is None:
        if os.path.exists(pipeline_name_or_path):
            config = verify_model_config_and_directory(pipeline_name_or_path)
        else:
            config = maybe_download_model_index(pipeline_name_or_path)
        logger.warning(
            "Trying to use the config from the model_index.json. FastVideo may not correctly identify the optimal config for this model in this situation."
        )

        pipeline_name = config["_class_name"]
        # Try to determine pipeline architecture for fallback
        for pipeline_type, detector in PIPELINE_DETECTOR.items():
            if detector(pipeline_name.lower()):
                pipeline_config_cls = PIPELINE_FALLBACK_CONFIG.get(
                    pipeline_type)
                break

        if pipeline_config_cls is not None:
            logger.warning(
                "No match found for pipeline %s, using fallback config %s.",
                pipeline_name_or_path, pipeline_config_cls)

    if pipeline_config_cls is None:
        raise ValueError(
            f"No match found for pipeline {pipeline_name_or_path}, please check the pipeline name or path."
        )

    return pipeline_config_cls
