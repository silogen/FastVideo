#!/bin/bash

num_gpus=1
export FASTVIDEO_ATTENTION_BACKEND=VIDEO_SPARSE_ATTN
export MODEL_BASE=FastVideo/FastWan2.1-T2V-1.3B-Diffusers
# export MODEL_BASE=hunyuanvideo-community/HunyuanVideo
# You can either use --prompt or --prompt-txt, but not both.
fastvideo generate \
    --model-path $MODEL_BASE \
    --sp-size $num_gpus \
    --tp-size 1 \
    --num-gpus $num_gpus \
    --height 480 \
    --width 848 \
    --num-frames 81 \
    --num-inference-steps 3 \
    --fps 16 \
    --prompt-txt assets/prompt.txt \
    --negative-prompt "Bright tones, overexposed, static, blurred details, subtitles, style, works, paintings, images, static, overall gray, worst quality, low quality, JPEG compression residue, ugly, incomplete, extra fingers, poorly drawn hands, poorly drawn faces, deformed, disfigured, misshapen limbs, fused fingers, still picture, messy background, three legs, many people in the background, walking backwards" \
    --seed 1024 \
    --output-path outputs_video_dmd/ \
    --VSA-sparsity 0.8 \
    --dmd-denoising-steps "1000,757,522"
