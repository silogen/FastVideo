# SPDX-License-Identifier: Apache-2.0
import json
import math
import os
import time
from collections.abc import Iterator
from typing import Any

import torch
import torch.distributed as dist
import torch.distributed.checkpoint as dcp
from einops import rearrange
from safetensors.torch import save_file

from fastvideo.distributed.parallel_state import (get_sp_parallel_rank,
                                                  get_sp_world_size)
from fastvideo.logger import init_logger
from fastvideo.training.checkpointing_utils import (ModelWrapper,
                                                    OptimizerWrapper,
                                                    RandomStateWrapper,
                                                    SchedulerWrapper)

logger = init_logger(__name__)

_HAS_ERRORED_CLIP_GRAD_NORM_WHILE_HANDLING_FAILING_DTENSOR_CASES = False


def gather_state_dict_on_cpu_rank0(
    model,
    device: torch.device | None = None,
) -> dict[str, Any]:
    rank = dist.get_rank()
    cpu_state_dict = {}
    sharded_sd = model.state_dict()
    param_requires_grad = set([
        k.replace("._checkpoint_wrapped_module.", ".")
        for k, v in dict(model.named_parameters()).items() if v.requires_grad
    ])
    for param_name, param in sharded_sd.items():
        if param_name not in param_requires_grad:
            continue
        if hasattr(param, "_local_tensor"):
            # DTensor case
            if param.is_cpu:
                # Gather directly on CPU
                param = param.full_tensor()
            else:
                if device is not None:
                    param = param.to(device)
                param = param.full_tensor()
        else:
            # Regular tensor case
            if param.is_cpu:
                pass
            else:
                if device is not None:
                    param = param.to(device)

        if rank == 0:
            cpu_state_dict[param_name] = param.cpu()

    return cpu_state_dict


def compute_density_for_timestep_sampling(
    weighting_scheme: str,
    batch_size: int,
    generator,
    logit_mean: float | None = None,
    logit_std: float | None = None,
    mode_scale: float | None = None,
):
    """
    Compute the density for sampling the timesteps when doing SD3 training.

    Courtesy: This was contributed by Rafie Walker in https://github.com/huggingface/diffusers/pull/8528.

    SD3 paper reference: https://arxiv.org/abs/2403.03206v1.
    """
    if weighting_scheme == "logit_normal":
        # See 3.1 in the SD3 paper ($rf/lognorm(0.00,1.00)$).
        u = torch.normal(
            mean=logit_mean,
            std=logit_std,
            size=(batch_size, ),
            device="cpu",
            generator=generator,
        )
        u = torch.nn.functional.sigmoid(u)
    elif weighting_scheme == "mode":
        u = torch.rand(size=(batch_size, ), device="cpu", generator=generator)
        u = 1 - u - mode_scale * (torch.cos(math.pi * u / 2)**2 - 1 + u)
    else:
        u = torch.rand(size=(batch_size, ), device="cpu", generator=generator)
    return u


def get_sigmas(noise_scheduler,
               device,
               timesteps,
               n_dim=4,
               dtype=torch.float32) -> torch.Tensor:
    sigmas = noise_scheduler.sigmas.to(device=device, dtype=dtype)
    schedule_timesteps = noise_scheduler.timesteps.to(device)
    timesteps = timesteps.to(device)
    step_indices = [(schedule_timesteps == t).nonzero().item()
                    for t in timesteps]

    sigma = sigmas[step_indices].flatten()
    while len(sigma.shape) < n_dim:
        sigma = sigma.unsqueeze(-1)
    return sigma


def save_checkpoint(transformer,
                    rank,
                    output_dir,
                    step,
                    optimizer=None,
                    dataloader=None,
                    scheduler=None,
                    noise_generator=None) -> None:
    """
    Save checkpoint following finetrainer's distributed checkpoint approach.
    Saves both distributed checkpoint and consolidated model weights.
    """
    save_dir = os.path.join(output_dir, f"checkpoint-{step}")
    os.makedirs(save_dir, exist_ok=True)

    states = {
        "model": ModelWrapper(transformer),
        "random_state": RandomStateWrapper(noise_generator),
    }

    if optimizer is not None:
        states["optimizer"] = OptimizerWrapper(transformer, optimizer)

    if dataloader is not None:
        states["dataloader"] = dataloader

    if scheduler is not None:
        states["scheduler"] = SchedulerWrapper(scheduler)
    dcp_dir = os.path.join(save_dir, "distributed_checkpoint")
    logger.info("rank: %s, saving distributed checkpoint to %s",
                rank,
                dcp_dir,
                local_main_process_only=False)

    begin_time = time.perf_counter()
    dcp.save(states, checkpoint_id=dcp_dir)
    end_time = time.perf_counter()

    logger.info("rank: %s, distributed checkpoint saved in %.2f seconds",
                rank,
                end_time - begin_time,
                local_main_process_only=False)

    cpu_state = gather_state_dict_on_cpu_rank0(transformer, device=None)

    if rank == 0:
        # Save model weights (consolidated)
        transformer_save_dir = os.path.join(save_dir, "transformer")
        os.makedirs(transformer_save_dir, exist_ok=True)
        weight_path = os.path.join(transformer_save_dir,
                                   "diffusion_pytorch_model.safetensors")
        logger.info("rank: %s, saving consolidated checkpoint to %s",
                    rank,
                    weight_path,
                    local_main_process_only=False)

        # Convert training format to diffusers format and save
        diffusers_state_dict = custom_to_hf_state_dict(
            cpu_state, transformer.reverse_param_names_mapping)
        save_file(diffusers_state_dict, weight_path)

        logger.info("rank: %s, consolidated checkpoint saved to %s",
                    rank,
                    weight_path,
                    local_main_process_only=False)

        # Save model config
        config_dict = transformer.hf_config
        if "dtype" in config_dict:
            del config_dict["dtype"]  # TODO
        config_path = os.path.join(transformer_save_dir, "config.json")
        # save dict as json
        with open(config_path, "w") as f:
            json.dump(config_dict, f, indent=4)
        logger.info("--> checkpoint saved at step %s to %s", step, weight_path)


def save_distillation_checkpoint(generator_transformer,
                                 fake_score_transformer,
                                 rank,
                                 output_dir,
                                 step,
                                 generator_optimizer=None,
                                 fake_score_optimizer=None,
                                 dataloader=None,
                                 generator_scheduler=None,
                                 fake_score_scheduler=None,
                                 noise_generator=None,
                                 only_save_generator_weight=False) -> None:
    """
    Save distillation checkpoint with both generator and fake_score models.
    Saves both distributed checkpoint and consolidated model weights.
    Only saves the generator model for inference (consolidated weights).
    
    Args:
        only_save_generator_weight: If True, only save the generator model weights for inference
                                   without saving distributed checkpoint for training resume.
    """
    save_dir = os.path.join(output_dir, f"checkpoint-{step}")
    os.makedirs(save_dir, exist_ok=True)

    # Create directories for models
    inference_save_dir = os.path.join(save_dir,
                                      "generator_inference_transformer")

    # Only save distributed checkpoint if not only saving generator weight
    if not only_save_generator_weight:
        # Save generator distributed checkpoint
        generator_states = {
            "model": ModelWrapper(generator_transformer),
        }
        if generator_optimizer is not None:
            generator_states["optimizer"] = OptimizerWrapper(
                generator_transformer, generator_optimizer)
        if dataloader is not None:
            generator_states["dataloader"] = dataloader
        if generator_scheduler is not None:
            generator_states["scheduler"] = SchedulerWrapper(
                generator_scheduler)

        generator_dcp_dir = os.path.join(save_dir, "distributed_checkpoint",
                                         "generator")
        logger.info("rank: %s, saving generator distributed checkpoint to %s",
                    rank,
                    generator_dcp_dir,
                    local_main_process_only=False)

        begin_time = time.perf_counter()
        dcp.save(generator_states, checkpoint_id=generator_dcp_dir)
        end_time = time.perf_counter()

        logger.info(
            "rank: %s, generator distributed checkpoint saved in %.2f seconds",
            rank,
            end_time - begin_time,
            local_main_process_only=False)

        # Save critic distributed checkpoint
        critic_states = {
            "model": ModelWrapper(fake_score_transformer),
        }
        if fake_score_optimizer is not None:
            critic_states["optimizer"] = OptimizerWrapper(
                fake_score_transformer, fake_score_optimizer)
        if dataloader is not None:
            critic_states["dataloader"] = dataloader
        if fake_score_scheduler is not None:
            critic_states["scheduler"] = SchedulerWrapper(fake_score_scheduler)

        critic_dcp_dir = os.path.join(save_dir, "distributed_checkpoint",
                                      "critic")
        logger.info("rank: %s, saving critic distributed checkpoint to %s",
                    rank,
                    critic_dcp_dir,
                    local_main_process_only=False)

        begin_time = time.perf_counter()
        dcp.save(critic_states, checkpoint_id=critic_dcp_dir)
        end_time = time.perf_counter()

        logger.info(
            "rank: %s, critic distributed checkpoint saved in %.2f seconds",
            rank,
            end_time - begin_time,
            local_main_process_only=False)

        # Save shared random state separately
        shared_states = {
            "random_state": RandomStateWrapper(noise_generator),
        }
        shared_dcp_dir = os.path.join(save_dir, "distributed_checkpoint",
                                      "shared")

        dcp.save(shared_states, checkpoint_id=shared_dcp_dir)

    else:
        logger.info(
            "rank: %s, skipping distributed checkpoint save (only_save_generator_weight=True)",
            rank,
            local_main_process_only=False)

    # Save generator model weights (consolidated) for inference
    cpu_state = gather_state_dict_on_cpu_rank0(generator_transformer,
                                               device=None)

    if rank == 0:
        # Save generator model weights (consolidated) for inference
        os.makedirs(inference_save_dir, exist_ok=True)
        weight_path = os.path.join(inference_save_dir,
                                   "diffusion_pytorch_model.safetensors")
        logger.info(
            "rank: %s, saving consolidated generator inference checkpoint to %s",
            rank,
            weight_path,
            local_main_process_only=False)

        # Convert training format to diffusers format and save
        diffusers_state_dict = custom_to_hf_state_dict(
            cpu_state, generator_transformer.reverse_param_names_mapping)
        save_file(diffusers_state_dict, weight_path)

        logger.info(
            "rank: %s, consolidated generator inference checkpoint saved to %s",
            rank,
            weight_path,
            local_main_process_only=False)

        # Save model config
        config_dict = generator_transformer.hf_config
        if "dtype" in config_dict:
            del config_dict["dtype"]  # TODO
        config_path = os.path.join(inference_save_dir, "config.json")
        # save dict as json
        with open(config_path, "w") as f:
            json.dump(config_dict, f, indent=4)
        logger.info("--> distillation checkpoint saved at step %s to %s", step,
                    weight_path)


def load_checkpoint(transformer,
                    rank,
                    checkpoint_path,
                    optimizer=None,
                    dataloader=None,
                    scheduler=None,
                    noise_generator=None) -> int:
    """
    Load checkpoint following finetrainer's distributed checkpoint approach.
    Returns the step number from which training should resume.
    """
    if not os.path.exists(checkpoint_path):
        logger.warning("Checkpoint path %s does not exist", checkpoint_path)
        return 0

    # Extract step number from checkpoint path
    step = int(os.path.basename(checkpoint_path).split('-')[-1])

    if rank == 0:
        logger.info("Loading checkpoint from step %s", step)

    dcp_dir = os.path.join(checkpoint_path, "distributed_checkpoint")

    if not os.path.exists(dcp_dir):
        logger.warning("Distributed checkpoint directory %s does not exist",
                       dcp_dir)
        return 0

    states = {
        "model": ModelWrapper(transformer),
        "random_state": RandomStateWrapper(noise_generator),
    }

    if optimizer is not None:
        states["optimizer"] = OptimizerWrapper(transformer, optimizer)

    if dataloader is not None:
        states["dataloader"] = dataloader

    if scheduler is not None:
        states["scheduler"] = SchedulerWrapper(scheduler)

    logger.info("rank: %s, loading distributed checkpoint from %s",
                rank,
                dcp_dir,
                local_main_process_only=False)

    begin_time = time.perf_counter()
    dcp.load(states, checkpoint_id=dcp_dir)
    end_time = time.perf_counter()

    logger.info("rank: %s, distributed checkpoint loaded in %.2f seconds",
                rank,
                end_time - begin_time,
                local_main_process_only=False)
    logger.info("--> checkpoint loaded from step %s", step)

    return step


def load_distillation_checkpoint(generator_transformer,
                                 fake_score_transformer,
                                 rank,
                                 checkpoint_path,
                                 generator_optimizer=None,
                                 fake_score_optimizer=None,
                                 dataloader=None,
                                 generator_scheduler=None,
                                 fake_score_scheduler=None,
                                 noise_generator=None) -> int:
    """
    Load distillation checkpoint with both generator and fake_score models.
    Returns the step number from which training should resume.
    """
    if not os.path.exists(checkpoint_path):
        logger.warning("Distillation checkpoint path %s does not exist",
                       checkpoint_path)
        return 0

    # Extract step number from checkpoint path
    step = int(os.path.basename(checkpoint_path).split('-')[-1])

    if rank == 0:
        logger.info("Loading distillation checkpoint from step %s", step)

    # Load generator distributed checkpoint
    generator_dcp_dir = os.path.join(checkpoint_path, "distributed_checkpoint",
                                     "generator")
    if not os.path.exists(generator_dcp_dir):
        logger.warning(
            "Generator distributed checkpoint directory %s does not exist",
            generator_dcp_dir)
        return 0

    generator_states = {
        "model": ModelWrapper(generator_transformer),
    }

    if generator_optimizer is not None:
        generator_states["optimizer"] = OptimizerWrapper(
            generator_transformer, generator_optimizer)

    if dataloader is not None:
        generator_states["dataloader"] = dataloader

    if generator_scheduler is not None:
        generator_states["scheduler"] = SchedulerWrapper(generator_scheduler)

    logger.info("rank: %s, loading generator distributed checkpoint from %s",
                rank,
                generator_dcp_dir,
                local_main_process_only=False)

    begin_time = time.perf_counter()
    dcp.load(generator_states, checkpoint_id=generator_dcp_dir)
    end_time = time.perf_counter()

    logger.info(
        "rank: %s, generator distributed checkpoint loaded in %.2f seconds",
        rank,
        end_time - begin_time,
        local_main_process_only=False)

    # Load critic distributed checkpoint
    critic_dcp_dir = os.path.join(checkpoint_path, "distributed_checkpoint",
                                  "critic")
    if not os.path.exists(critic_dcp_dir):
        logger.warning(
            "Critic distributed checkpoint directory %s does not exist",
            critic_dcp_dir)
        return 0

    critic_states = {
        "model": ModelWrapper(fake_score_transformer),
    }

    if fake_score_optimizer is not None:
        critic_states["optimizer"] = OptimizerWrapper(fake_score_transformer,
                                                      fake_score_optimizer)

    if dataloader is not None:
        critic_states["dataloader"] = dataloader

    if fake_score_scheduler is not None:
        critic_states["scheduler"] = SchedulerWrapper(fake_score_scheduler)

    logger.info("rank: %s, loading critic distributed checkpoint from %s",
                rank,
                critic_dcp_dir,
                local_main_process_only=False)

    begin_time = time.perf_counter()
    dcp.load(critic_states, checkpoint_id=critic_dcp_dir)
    end_time = time.perf_counter()

    logger.info(
        "rank: %s, critic distributed checkpoint loaded in %.2f seconds",
        rank,
        end_time - begin_time,
        local_main_process_only=False)

    # Load shared random state
    shared_dcp_dir = os.path.join(checkpoint_path, "distributed_checkpoint",
                                  "shared")
    if not os.path.exists(shared_dcp_dir):
        logger.warning("Shared random state directory %s does not exist",
                       shared_dcp_dir)
        return 0

    shared_states = {
        "random_state": RandomStateWrapper(noise_generator),
    }

    begin_time = time.perf_counter()
    dcp.load(shared_states, checkpoint_id=shared_dcp_dir)
    end_time = time.perf_counter()

    logger.info("rank: %s, shared random state loaded in %.2f seconds",
                rank,
                end_time - begin_time,
                local_main_process_only=False)
    logger.info("--> distillation checkpoint loaded from step %s", step)
    return step


def normalize_dit_input(model_type, latents, vae) -> torch.Tensor:
    if model_type == "hunyuan_hf" or model_type == "hunyuan":
        return latents * 0.476986
    elif model_type == "wan":
        latents_mean = torch.tensor(vae.latents_mean)
        latents_std = 1.0 / torch.tensor(vae.latents_std)

        latents_mean = latents_mean.view(1, -1, 1, 1,
                                         1).to(device=latents.device)
        latents_std = latents_std.view(1, -1, 1, 1, 1).to(device=latents.device)
        latents = ((latents.float() - latents_mean) * latents_std).to(latents)
        return latents
    else:
        raise NotImplementedError(f"model_type {model_type} not supported")


def shard_latents_across_sp(latents: torch.Tensor,
                            num_latent_t: int) -> torch.Tensor:
    sp_world_size = get_sp_world_size()
    rank_in_sp_group = get_sp_parallel_rank()
    latents = latents[:, :, :num_latent_t]
    if sp_world_size > 1:
        latents = rearrange(latents,
                            "b c (n s) h w -> b c n s h w",
                            n=sp_world_size).contiguous()
        latents = latents[:, :, rank_in_sp_group, :, :, :]
    return latents


def clip_grad_norm_while_handling_failing_dtensor_cases(
    parameters: torch.Tensor | list[torch.Tensor],
    max_norm: float,
    norm_type: float = 2.0,
    error_if_nonfinite: bool = False,
    foreach: bool | None = None,
    pp_mesh: torch.distributed.device_mesh.DeviceMesh | None = None,
) -> torch.Tensor | None:
    global _HAS_ERRORED_CLIP_GRAD_NORM_WHILE_HANDLING_FAILING_DTENSOR_CASES

    if not _HAS_ERRORED_CLIP_GRAD_NORM_WHILE_HANDLING_FAILING_DTENSOR_CASES:
        try:
            return clip_grad_norm_(parameters, max_norm, norm_type,
                                   error_if_nonfinite, foreach, pp_mesh)
        except NotImplementedError as e:
            if "DTensor does not support cross-mesh operation" in str(e):
                # https://github.com/pytorch/pytorch/issues/134212
                logger.warning(
                    "DTensor does not support cross-mesh operation. If you haven't fully tensor-parallelized your "
                    "model, while combining other parallelisms such as FSDP, it could be the reason for this error. "
                    "Gradient clipping will be skipped and gradient norm will not be logged."
                )
        except Exception as e:
            logger.warning(
                "An error occurred while clipping gradients: %s. Gradient clipping will be skipped and gradient "
                "norm will not be logged.", e)
            _HAS_ERRORED_CLIP_GRAD_NORM_WHILE_HANDLING_FAILING_DTENSOR_CASES = True
    return None


# Copied from https://github.com/pytorch/torchtitan/blob/4a169701555ab9bd6ca3769f9650ae3386b84c6e/torchtitan/utils.py#L362
@torch.no_grad()
def clip_grad_norm_(
    parameters: torch.Tensor | list[torch.Tensor],
    max_norm: float,
    norm_type: float = 2.0,
    error_if_nonfinite: bool = False,
    foreach: bool | None = None,
    pp_mesh: torch.distributed.device_mesh.DeviceMesh | None = None,
) -> torch.Tensor:
    r"""
    Clip the gradient norm of parameters.

    Gradient norm clipping requires computing the gradient norm over the entire model.
    `torch.nn.utils.clip_grad_norm_` only computes gradient norm along DP/FSDP/TP dimensions.
    We need to manually reduce the gradient norm across PP stages.
    See https://github.com/pytorch/torchtitan/issues/596 for details.

    Args:
        parameters (`torch.Tensor` or `List[torch.Tensor]`):
            Tensors that will have gradients normalized.
        max_norm (`float`):
            Maximum norm of the gradients after clipping.
        norm_type (`float`, defaults to `2.0`):
            Type of p-norm to use. Can be `inf` for infinity norm.
        error_if_nonfinite (`bool`, defaults to `False`):
            If `True`, an error is thrown if the total norm of the gradients from `parameters` is `nan`, `inf`, or `-inf`.
        foreach (`bool`, defaults to `None`):
            Use the faster foreach-based implementation. If `None`, use the foreach implementation for CUDA and CPU native tensors
            and silently fall back to the slow implementation for other device types.
        pp_mesh (`torch.distributed.device_mesh.DeviceMesh`, defaults to `None`):
            Pipeline parallel device mesh. If not `None`, will reduce gradient norm across PP stages.

    Returns:
        `torch.Tensor`:
            Total norm of the gradients
    """
    grads = [p.grad for p in parameters if p.grad is not None]

    # TODO(aryan): Wait for next Pytorch release to use `torch.nn.utils.get_total_norm`
    # total_norm = torch.nn.utils.get_total_norm(grads, norm_type, error_if_nonfinite, foreach)
    total_norm = _get_total_norm(grads, norm_type, error_if_nonfinite, foreach)

    # If total_norm is a DTensor, the placements must be `torch.distributed._tensor.ops.math_ops._NormPartial`.
    # We can simply reduce the DTensor to get the total norm in this tensor's process group
    # and then convert it to a local tensor.
    # It has two purposes:
    #   1. to make sure the total norm is computed correctly when PP is used (see below)
    #   2. to return a reduced total_norm tensor whose .item() would return the correct value
    if isinstance(total_norm, torch.distributed.tensor.DTensor):
        # Will reach here if any non-PP parallelism is used.
        # If only using PP, total_norm will be a local tensor.
        total_norm = total_norm.full_tensor()

    if pp_mesh is not None:
        raise NotImplementedError("Pipeline parallel is not supported")
        if math.isinf(norm_type):
            dist.all_reduce(total_norm,
                            op=dist.ReduceOp.MAX,
                            group=pp_mesh.get_group())
        else:
            total_norm **= norm_type
            dist.all_reduce(total_norm,
                            op=dist.ReduceOp.SUM,
                            group=pp_mesh.get_group())
            total_norm **= 1.0 / norm_type

    _clip_grads_with_norm_(parameters, max_norm, total_norm, foreach)
    return total_norm


@torch.no_grad()
def _clip_grads_with_norm_(
    parameters: torch.Tensor | list[torch.Tensor],
    max_norm: float,
    total_norm: torch.Tensor,
    foreach: bool | None = None,
) -> None:
    if isinstance(parameters, torch.Tensor):
        parameters = [parameters]
    grads = [p.grad for p in parameters if p.grad is not None]
    max_norm = float(max_norm)
    if len(grads) == 0:
        return
    grouped_grads: dict[tuple[torch.device, torch.dtype],
                        tuple[list[list[torch.Tensor]],
                              list[int]]] = (_group_tensors_by_device_and_dtype(
                                  [grads]))  # type: ignore[assignment]

    clip_coef = max_norm / (total_norm + 1e-6)

    # Note: multiplying by the clamped coef is redundant when the coef is clamped to 1, but doing so
    # avoids a `if clip_coef < 1:` conditional which can require a CPU <=> device synchronization
    # when the gradients do not reside in CPU memory.
    clip_coef_clamped = torch.clamp(clip_coef, max=1.0)
    for (device, _), ([device_grads], _) in grouped_grads.items():
        if (foreach is None and _has_foreach_support(device_grads, device)) or (
                foreach and _device_has_foreach_support(device)):
            torch._foreach_mul_(device_grads, clip_coef_clamped.to(device))
        elif foreach:
            raise RuntimeError(
                f"foreach=True was passed, but can't use the foreach API on {device.type} tensors"
            )
        else:
            clip_coef_clamped_device = clip_coef_clamped.to(device)
            for g in device_grads:
                g.mul_(clip_coef_clamped_device)


def _get_total_norm(
    tensors: torch.Tensor | list[torch.Tensor],
    norm_type: float = 2.0,
    error_if_nonfinite: bool = False,
    foreach: bool | None = None,
) -> torch.Tensor:
    tensors = [tensors] if isinstance(tensors, torch.Tensor) else list(tensors)
    norm_type = float(norm_type)
    if len(tensors) == 0:
        return torch.tensor(0.0)
    first_device = tensors[0].device
    grouped_tensors: dict[tuple[torch.device, torch.dtype],
                          tuple[list[list[torch.Tensor]], list[int]]] = (
                              _group_tensors_by_device_and_dtype(
                                  [tensors]  # type: ignore[list-item]
                              ))  # type: ignore[assignment]

    norms: list[torch.Tensor] = []
    for (device, _), ([device_tensors], _) in grouped_tensors.items():
        local_tensors = [
            t.to_local()
            if isinstance(t, torch.distributed.tensor.DTensor) else t
            for t in device_tensors
        ]
        if (foreach is None and _has_foreach_support(local_tensors, device)
            ) or (foreach and _device_has_foreach_support(device)):
            norms.extend(torch._foreach_norm(local_tensors, norm_type))
        elif foreach:
            raise RuntimeError(
                f"foreach=True was passed, but can't use the foreach API on {device.type} tensors"
            )
        else:
            norms.extend(
                [torch.linalg.vector_norm(g, norm_type) for g in local_tensors])

    total_norm = torch.linalg.vector_norm(
        torch.stack([norm.to(first_device) for norm in norms]), norm_type)

    if error_if_nonfinite and torch.logical_or(total_norm.isnan(),
                                               total_norm.isinf()):
        raise RuntimeError(
            f"The total norm of order {norm_type} for gradients from "
            "`parameters` is non-finite, so it cannot be clipped. To disable "
            "this error and scale the gradients by the non-finite norm anyway, "
            "set `error_if_nonfinite=False`")
    return total_norm


def _get_foreach_kernels_supported_devices() -> list[str]:
    r"""Return the device type list that supports foreach kernels."""
    return ["cuda", "xpu", torch._C._get_privateuse1_backend_name()]


@torch.no_grad()
def _group_tensors_by_device_and_dtype(
    tensorlistlist: list[list[torch.Tensor | None]],
    with_indices: bool = False,
) -> dict[tuple[torch.device, torch.dtype], tuple[
        list[list[torch.Tensor | None]], list[int]]]:
    return torch._C._group_tensors_by_device_and_dtype(  # type: ignore[no-any-return]
        tensorlistlist, with_indices)


def _device_has_foreach_support(device: torch.device) -> bool:
    return device.type in (_get_foreach_kernels_supported_devices() +
                           ["cpu"]) and not torch.jit.is_scripting()


def _has_foreach_support(tensors: list[torch.Tensor],
                         device: torch.device) -> bool:
    return _device_has_foreach_support(device) and all(
        t is None or type(t) in [torch.Tensor] for t in tensors)


def custom_to_hf_state_dict(
    state_dict: dict[str, Any] | Iterator[tuple[str, torch.Tensor]],
    reverse_param_names_mapping: dict[str, tuple[str, int,
                                                 int]]) -> dict[str, Any]:
    """
    Convert fastvideo's custom model format to diffusers format using reverse_param_names_mapping.
    
    Args:
        state_dict: State dict in fastvideo's custom format
        reverse_param_names_mapping: Reverse mapping from fastvideo's custom format to diffusers format
        
    Returns:
        State dict in diffusers format
    """
    assert len(
        reverse_param_names_mapping) > 0, "reverse_param_names_mapping is empty"
    if isinstance(state_dict, Iterator):
        state_dict = dict(state_dict)
    new_state_dict = {}
    # Group parameters that need to be split (merged parameters)
    merge_groups: dict[str, list[tuple[str, int, int]]] = {}

    # First pass: collect all merge groups
    for training_key, (
            diffusers_key, merge_index,
            num_params_to_merge) in reverse_param_names_mapping.items():
        if merge_index is not None:
            # This is a merged parameter that needs to be split
            if training_key not in merge_groups:
                merge_groups[training_key] = []
            merge_groups[training_key].append(
                (diffusers_key, merge_index, num_params_to_merge))

    # Second pass: handle merged parameters by splitting them
    used_keys = set()
    for training_key, splits in merge_groups.items():
        if training_key in state_dict:
            v = state_dict[training_key]
            # Sort by merge_index to ensure correct order
            splits.sort(key=lambda x: x[1])
            total = splits[0][2]
            split_size = v.shape[0] // total
            split_tensors = torch.split(v, split_size, dim=0)

            for diffusers_key, split_index, _ in splits:
                new_state_dict[diffusers_key] = split_tensors[split_index]
            used_keys.add(training_key)

    # Third pass: handle regular parameters (direct mappings)
    for training_key, v in state_dict.items():
        if training_key in used_keys:
            continue

        if training_key in reverse_param_names_mapping:
            diffusers_key, merge_index, _ = reverse_param_names_mapping[
                training_key]
            if merge_index is None:
                # Direct mapping
                new_state_dict[diffusers_key] = v
        else:
            # No mapping found, keep as is
            new_state_dict[training_key] = v

    return new_state_dict


def pred_noise_to_pred_video(pred_noise: torch.Tensor,
                             noise_input_latent: torch.Tensor,
                             timestep: torch.Tensor,
                             scheduler: Any) -> torch.Tensor:
    """
    Convert predicted noise to clean latent.
    """
    timestep = timestep.expand(noise_input_latent.shape[0])
    dtype = pred_noise.dtype
    device = pred_noise.device
    pred_noise = pred_noise.float().to(device)
    noise_input_latent = noise_input_latent.float().to(device)
    sigmas = scheduler.sigmas.float().to(device)
    timesteps = scheduler.timesteps.float().to(device)
    timestep_id = torch.argmin(
        (timesteps.unsqueeze(0) - timestep.unsqueeze(1)).abs(), dim=1)
    sigma_t = sigmas[timestep_id].reshape(-1, 1, 1, 1)
    pred_video = noise_input_latent - sigma_t * pred_noise
    return pred_video.to(dtype)


def shift_timestep(timestep: torch.Tensor, shift: float,
                   num_train_timestep: float) -> torch.Tensor:
    if shift == 1:
        return timestep
    t = timestep / num_train_timestep
    denominator = 1 + (shift - 1) * t
    return num_train_timestep * (shift * t / denominator)
