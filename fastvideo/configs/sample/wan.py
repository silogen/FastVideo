# SPDX-License-Identifier: Apache-2.0
from dataclasses import dataclass, field

from fastvideo.configs.sample.base import SamplingParam
from fastvideo.configs.sample.teacache import WanTeaCacheParams


@dataclass
class WanT2V_1_3B_SamplingParam(SamplingParam):
    # Video parameters
    height: int = 480
    width: int = 832
    num_frames: int = 81
    fps: int = 16

    # Denoising stage
    guidance_scale: float = 3.0
    negative_prompt: str = "Bright tones, overexposed, static, blurred details, subtitles, style, works, paintings, images, static, overall gray, worst quality, low quality, JPEG compression residue, ugly, incomplete, extra fingers, poorly drawn hands, poorly drawn faces, deformed, disfigured, misshapen limbs, fused fingers, still picture, messy background, three legs, many people in the background, walking backwards"
    num_inference_steps: int = 50

    teacache_params: WanTeaCacheParams = field(
        default_factory=lambda: WanTeaCacheParams(
            teacache_thresh=0.08,
            ret_steps_coeffs=[
                -5.21862437e+04, 9.23041404e+03, -5.28275948e+02,
                1.36987616e+01, -4.99875664e-02
            ],
            non_ret_steps_coeffs=[
                2.39676752e+03, -1.31110545e+03, 2.01331979e+02,
                -8.29855975e+00, 1.37887774e-01
            ]))


@dataclass
class WanT2V_14B_SamplingParam(SamplingParam):
    # Video parameters
    height: int = 720
    width: int = 1280
    num_frames: int = 81
    fps: int = 16

    # Denoising stage
    guidance_scale: float = 5.0
    negative_prompt: str = "Bright tones, overexposed, static, blurred details, subtitles, style, works, paintings, images, static, overall gray, worst quality, low quality, JPEG compression residue, ugly, incomplete, extra fingers, poorly drawn hands, poorly drawn faces, deformed, disfigured, misshapen limbs, fused fingers, still picture, messy background, three legs, many people in the background, walking backwards"
    num_inference_steps: int = 50

    teacache_params: WanTeaCacheParams = field(
        default_factory=lambda: WanTeaCacheParams(
            teacache_thresh=0.20,
            use_ret_steps=False,
            ret_steps_coeffs=[
                -3.03318725e+05, 4.90537029e+04, -2.65530556e+03,
                5.87365115e+01, -3.15583525e-01
            ],
            non_ret_steps_coeffs=[
                -5784.54975374, 5449.50911966, -1811.16591783, 256.27178429,
                -13.02252404
            ]))


@dataclass
class WanI2V_14B_480P_SamplingParam(WanT2V_1_3B_SamplingParam):
    # Denoising stage
    guidance_scale: float = 5.0
    num_inference_steps: int = 40

    teacache_params: WanTeaCacheParams = field(
        default_factory=lambda: WanTeaCacheParams(
            teacache_thresh=0.26,
            ret_steps_coeffs=[
                -3.03318725e+05, 4.90537029e+04, -2.65530556e+03,
                5.87365115e+01, -3.15583525e-01
            ],
            non_ret_steps_coeffs=[
                -5784.54975374, 5449.50911966, -1811.16591783, 256.27178429,
                -13.02252404
            ]))


@dataclass
class WanI2V_14B_720P_SamplingParam(WanT2V_14B_SamplingParam):
    # Denoising stage
    guidance_scale: float = 5.0
    num_inference_steps: int = 40

    teacache_params: WanTeaCacheParams = field(
        default_factory=lambda: WanTeaCacheParams(
            teacache_thresh=0.3,
            ret_steps_coeffs=[
                -3.03318725e+05, 4.90537029e+04, -2.65530556e+03,
                5.87365115e+01, -3.15583525e-01
            ],
            non_ret_steps_coeffs=[
                -5784.54975374, 5449.50911966, -1811.16591783, 256.27178429,
                -13.02252404
            ]))


@dataclass
class FastWanT2V480PConfig(WanT2V_1_3B_SamplingParam):
    # DMD parameters
    # dmd_denoising_steps: list[int] | None = field(default_factory=lambda: [1000, 757, 522])
    num_inference_steps: int = 3
    num_frames: int = 61
    height: int = 448
    width: int = 832
    fps: int = 16


# =============================================
# ============= Wan2.2 TI2V Models =============
# =============================================
@dataclass
class Wan2_2_Base_SamplingParam(SamplingParam):
    """Sampling parameters for Wan2.2 TI2V 5B model."""
    negative_prompt: str | None = "色调艳丽，过曝，静态，细节模糊不清，字幕，风格，作品，画作，画面，静止，整体发灰，最差质量，低质量，JPEG压缩残留，丑陋的，残缺的，多余的手指，画得不好的手部，画得不好的脸部，畸形的，毁容的，形态畸形的肢体，手指融合，静止不动的画面，杂乱的背景，三条腿，背景人很多，倒着走"


@dataclass
class Wan2_2_TI2V_5B_SamplingParam(Wan2_2_Base_SamplingParam):
    """Sampling parameters for Wan2.2 TI2V 5B model."""
    height: int = 704
    width: int = 1280
    num_frames: int = 121
    fps: int = 24
    guidance_scale: float = 5.0
    num_inference_steps: int = 50


@dataclass
class Wan2_2_T2V_A14B_SamplingParam(Wan2_2_Base_SamplingParam):
    pass


@dataclass
class Wan2_2_I2V_A14B_SamplingParam(Wan2_2_Base_SamplingParam):
    pass
