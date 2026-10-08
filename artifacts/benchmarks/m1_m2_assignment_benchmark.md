# M1-to-M2 Damage-to-Part Assignment Benchmark Report

**Date:** 2026-10-03T17:10:36.923768+00:00  
**Test Images Evaluated:** 132 images  

## 1. Cross-Architecture Benchmark Summary (Standard Config: min_damage_pixels=256)

| Pair ID | M1 Parts Model | M2 Damage Model | Detected Regions | Assigned Regions | Assignment Rate | Mean Containment | Mean BG Containment |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| M1_SegFo x M2_SegFo | M1_SegFormer_B3 | M2_SegFormer_B3 | 469 | 367 | **78.2%** | 0.802 | 0.030 |
| M1_YOLOv x M2_SegFo | M1_YOLOv8m_seg | M2_SegFormer_B3 | 469 | 363 | **77.4%** | 0.811 | 0.052 |
| M1_SegFo x M2_DeepL | M1_SegFormer_B3 | M2_DeepLabV3_ResNet50 | 349 | 268 | **76.8%** | 0.795 | 0.022 |
| M1_YOLOv x M2_DeepL | M1_YOLOv8m_seg | M2_DeepLabV3_ResNet50 | 349 | 270 | **77.4%** | 0.803 | 0.041 |
| M1_YOLOv x M2_YOLOv | M1_YOLOv8m_seg | M2_YOLOv8m_seg | 202 | 165 | **81.7%** | 0.830 | 0.055 |
| M1_SegFo x M2_YOLOv | M1_SegFormer_B3 | M2_YOLOv8m_seg | 202 | 160 | **79.2%** | 0.789 | 0.036 |

## 2. Sensitivity Benchmark (Fine Config: min_damage_pixels=64)

| Pair ID | M1 Parts Model | M2 Damage Model | Detected Regions | Assigned Regions | Assignment Rate | Mean Containment | Mean BG Containment |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| M1_SegFo x M2_SegFo | M1_SegFormer_B3 | M2_SegFormer_B3 | 859 | 710 | **82.7%** | 0.842 | 0.027 |
| M1_YOLOv x M2_SegFo | M1_YOLOv8m_seg | M2_SegFormer_B3 | 859 | 711 | **82.8%** | 0.841 | 0.049 |
| M1_SegFo x M2_DeepL | M1_SegFormer_B3 | M2_DeepLabV3_ResNet50 | 491 | 388 | **79.0%** | 0.826 | 0.019 |
| M1_YOLOv x M2_DeepL | M1_YOLOv8m_seg | M2_DeepLabV3_ResNet50 | 491 | 399 | **81.3%** | 0.826 | 0.040 |
| M1_YOLOv x M2_YOLOv | M1_YOLOv8m_seg | M2_YOLOv8m_seg | 290 | 234 | **80.7%** | 0.827 | 0.063 |
| M1_SegFo x M2_YOLOv | M1_SegFormer_B3 | M2_YOLOv8m_seg | 290 | 230 | **79.3%** | 0.792 | 0.044 |

## 3. Unresolved Failure Analysis (Standard Config)

| Pairing | Ambiguous Between Parts | Mostly Background | Below Containment Threshold | No Part Overlap |
| :--- | :--- | :--- | :--- | :--- |
| M1_SegFormer_B3 x M2_SegFormer_B3 | 71 | 5 | 25 | 1 |
| M1_YOLOv8m_seg x M2_SegFormer_B3 | 62 | 7 | 34 | 3 |
| M1_SegFormer_B3 x M2_DeepLabV3_ResNet50 | 56 | 1 | 24 | 0 |
| M1_YOLOv8m_seg x M2_DeepLabV3_ResNet50 | 52 | 3 | 24 | 0 |
| M1_YOLOv8m_seg x M2_YOLOv8m_seg | 23 | 1 | 11 | 2 |
| M1_SegFormer_B3 x M2_YOLOv8m_seg | 26 | 2 | 13 | 1 |