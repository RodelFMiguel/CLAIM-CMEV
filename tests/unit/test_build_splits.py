"""Unit tests for the group-aware split manifest generator."""
from __future__ import annotations

from pipelines.vision.build_splits import create_splits


def test_create_splits_disjoint_and_total():
    # Mock records
    parts_records = [
        {
            "image_name": f"img_{i}.png",
            "image_path": f"path/to/img_{i}.png",
            "image_sha256": f"hash_{i}",
            "mask_path": f"path/to/mask_{i}.png",
            "mask_sha256": f"maskhash_{i}",
            "width": 640,
            "height": 480,
            "class_pixel_counts": {"hood": 100},
            "class_object_counts": {"hood": 1},
        }
        for i in range(100)
    ]
    union_hashes = [f"hash_{i}" for i in range(100)] + [f"extra_hash_{j}" for j in range(20)]
    shared_hashes = {f"hash_{i}" for i in range(10)}

    splits = create_splits(
        parts_records,
        union_hashes,
        shared_hashes,
        seed=42,
        train_ratio=0.70,
        val_ratio=0.15,
    )

    train_hashes = {r["content_sha256"] for r in splits["train"]}
    val_hashes = {r["content_sha256"] for r in splits["val"]}
    test_hashes = {r["content_sha256"] for r in splits["test"]}

    # Disjointness
    assert train_hashes.isdisjoint(val_hashes)
    assert train_hashes.isdisjoint(test_hashes)
    assert val_hashes.isdisjoint(test_hashes)

    # Totality
    assert len(splits["train"]) + len(splits["val"]) + len(splits["test"]) == 100

    # Verify shared image flagging
    shared_entries = [r for r in splits["train"] + splits["val"] + splits["test"] if r["is_shared_with_damage"]]
    assert len(shared_entries) == 10


def test_create_splits_deterministic():
    parts_records = [
        {
            "image_name": f"img_{i}.png",
            "image_path": f"path/to/img_{i}.png",
            "image_sha256": f"hash_{i}",
            "mask_path": f"path/to/mask_{i}.png",
            "mask_sha256": f"maskhash_{i}",
            "width": 640,
            "height": 480,
            "class_pixel_counts": {"hood": 100},
            "class_object_counts": {"hood": 1},
        }
        for i in range(50)
    ]
    union_hashes = [f"hash_{i}" for i in range(50)]
    shared_hashes = set()

    splits1 = create_splits(parts_records, union_hashes, shared_hashes, seed=20260922)
    splits2 = create_splits(parts_records, union_hashes, shared_hashes, seed=20260922)

    assert [r["example_id"] for r in splits1["train"]] == [r["example_id"] for r in splits2["train"]]
    assert [r["example_id"] for r in splits1["val"]] == [r["example_id"] for r in splits2["val"]]
    assert [r["example_id"] for r in splits1["test"]] == [r["example_id"] for r in splits2["test"]]
