"""Offline failure review and train-only memorization diagnostics; never benchmarks."""
from dataclasses import asdict
from pathlib import Path
import json
import random

import numpy as np
from PIL import Image

from pipelines.vision.training import (
    SegmentationDataset, _verify_records, build_model, confusion_counts,
    metrics_from_confusion, segmentation_loss,
)


def rank_misses(model, records, config, names, device, focus_names):
    """Score class-present train/val images at the saved model's input resolution."""
    import torch
    import pandas as pd
    if any(r['split'] not in {'train', 'val'} for r in records):
        raise ValueError('Audit only train/val; keep test and reserved images untouched')
    if not focus_names or any(n not in names[1:] for n in focus_names):
        raise ValueError('Specify known foreground focus classes')
    _verify_records(records)
    data = SegmentationDataset(records, config, training=False, num_classes=len(names))
    rows = []
    model.eval()
    with torch.inference_mode():
        for i, record in enumerate(records):
            item = data[i]
            truth = item['labels'].numpy()
            pred = model(item['pixel_values'][None].to(device)).argmax(1)[0].cpu().numpy()
            valid = truth != 255
            with Image.open(record['mask_path']) as im:
                native = np.asarray(im)
            for name in focus_names:
                cid = names.index(name)
                original_count = int((native == cid).sum())
                if not original_count:
                    continue
                target = truth == cid
                count = int(target.sum())
                tp = int((target & (pred == cid)).sum())
                predicted = int(((pred == cid) & valid).sum())
                rows.append(dict(sample_id=record['sample_id'], split=record['split'],
                    focus_class=name, native_pixels=original_count, model_pixels=count,
                    erased_by_resize=count == 0, recall=tp/count if count else None,
                    precision=tp/predicted if predicted else None,
                    missed_as_background=int((target & (pred == 0)).sum()),
                    missed_as_other_damage=int((target & (pred != 0) & (pred != cid)).sum()),
                    image_sha256=record.get('image_sha256'), mask_sha256=record.get('mask_sha256')))
            if (i+1) % 25 == 0 or i+1 == len(records):
                print(f'Audit inference: {i+1}/{len(records)} images', flush=True)
    return pd.DataFrame(rows)


def select_misses(scores, focus_names, count=24, max_recall=0.5):
    """Alternate class queues; export distinct images, never pad with successes."""
    import pandas as pd
    if scores.empty:
        raise ValueError('No focus-class labels found in this audit cohort')
    queues = {}
    for name in focus_names:
        group = scores[(scores.focus_class == name) &
                       (scores.erased_by_resize | (scores.recall < max_recall))]
        queues[name] = iter(group.sort_values(
            ['erased_by_resize', 'recall', 'sample_id'],
            ascending=[False, True, True], na_position='first').to_dict('records'))
    selected, seen = [], set()
    while len(selected) < count:
        added = False
        for queue in queues.values():
            for row in queue:
                if row['sample_id'] not in seen:
                    selected.append(row)
                    seen.add(row['sample_id'])
                    added = True
                    break
            if len(selected) >= count:
                break
        if not added:
            break
    return pd.DataFrame(selected, columns=scores.columns)


def render_review(model, record, config, names, device, focus_name, zoom_size=256):
    """Native-frame overview/zoom; predictions are inverse-letterboxed for display."""
    import torch
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch, Rectangle
    item = SegmentationDataset([record], config, num_classes=len(names))[0]
    model.eval()
    with torch.inference_mode():
        pred = model(item['pixel_values'][None].to(device)).argmax(1)[0].cpu().numpy()
    with Image.open(record['image_path']) as im:
        rgb = np.asarray(im.convert('RGB'))
    with Image.open(record['mask_path']) as im:
        truth = np.asarray(im)
    h, w = truth.shape
    scale = min(config.image_size / w, config.image_size / h)
    rw, rh = max(1, round(w * scale)), max(1, round(h * scale))
    left, top = (config.image_size-rw)//2, (config.image_size-rh)//2
    native_pred = np.asarray(Image.fromarray(pred[top:top+rh, left:left+rw].astype('uint8')).resize(
        (w, h), Image.Resampling.NEAREST))
    ys, xs = np.nonzero(truth == names.index(focus_name))
    if not len(xs):
        raise ValueError('Focus class is absent from this source mask')
    missed = native_pred[ys, xs] != names.index(focus_name)
    if missed.any():
        xs, ys = xs[missed], ys[missed]
    anchor = len(xs)//2
    x0 = max(0, min(w-zoom_size, int(xs[anchor])-zoom_size//2))
    y0 = max(0, min(h-zoom_size, int(ys[anchor])-zoom_size//2))
    x1, y1 = min(w, x0+zoom_size), min(h, y0+zoom_size)
    palette = plt.get_cmap('turbo', len(names))
    fig, axes = plt.subplots(2, 3, figsize=(15, 10))
    for row, view in enumerate((np.s_[:, :], np.s_[y0:y1, x0:x1])):
        for column, labels in enumerate((None, truth, native_pred)):
            ax = axes[row, column]
            ax.imshow(rgb[view])
            if labels is not None:
                visible = np.ma.masked_where((labels == 0) | (truth == 255), labels)
                ax.imshow(visible[view], cmap=palette, vmin=0, vmax=len(names)-1,
                          alpha=0.55, interpolation='nearest')
                ignored = np.zeros((*truth[view].shape, 4))
                ignored[truth[view] == 255] = (1, 0, 1, 0.7)
                ax.imshow(ignored)
            if row == 0:
                ax.add_patch(Rectangle((x0, y0), x1-x0, y1-y0, fill=False, edgecolor='white'))
            ax.set_title(('Original photograph', 'Prepared target', 'Model prediction')[column] +
                         (' — close-up' if row else ''))
            ax.axis('off')
    handles = [Patch(color=palette(i), label=n) for i, n in enumerate(names) if i]
    handles.append(Patch(color='magenta', label='ignore 255'))
    fig.legend(handles=handles, loc='lower center', ncol=5, fontsize=8)
    fig.suptitle(f"{record['sample_id']} | {focus_name} | model input {config.image_size}px")
    fig.subplots_adjust(left=.02, right=.98, top=.9, bottom=.12, wspace=.04, hspace=.3)
    return fig


def reviewed_training_records(review, records, selected_ids):
    """Require explicit clean reviews and original training membership for every ID."""
    if not selected_ids or len(set(selected_ids)) != len(selected_ids):
        raise ValueError('Choose unique reviewed training sample IDs')
    lookup = {r['sample_id']: r for r in records}
    chosen = []
    for sample_id in selected_ids:
        record = lookup.get(sample_id)
        rows = review[review.sample_id == sample_id]
        if record is None or record['split'] != 'train':
            raise ValueError('Tiny fitting accepts original training records only')
        if len(rows) != 1 or rows.iloc[0]['review_status'] != 'clean':
            raise ValueError(f'{sample_id} needs one explicit clean review')
        row = rows.iloc[0]
        if row['split'] != 'train' or any(row[k] != record[k] for k in ('image_sha256', 'mask_sha256')):
            raise ValueError('Review provenance differs from original training record')
        chosen.append(record)
    _verify_records(chosen)
    return chosen


def fit_tiny_subset(records, config, names, device, output_dir, *, steps=200,
                    report_every=25, source_provenance=None):
    """Fresh pretrained initialization; fit and measure the SAME train images.

    This deliberately has no validation/model selection and cannot be compared
    with held-out training runs. The caller must resolve clean reviewed records.
    """
    import torch
    from torch.utils.data import DataLoader
    if not records or any(r['split'] != 'train' for r in records):
        raise ValueError('Only nonempty original training subsets can be fitted')
    if steps < 1 or report_every < 1:
        raise ValueError('Steps and report interval must be positive')
    _verify_records(records)
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=False)
    random.seed(config.seed)
    np.random.seed(config.seed)
    torch.manual_seed(config.seed)
    data = SegmentationDataset(records, config, training=False, num_classes=len(names))
    loader = DataLoader(data, batch_size=config.batch_size, shuffle=False, num_workers=0)
    meta = dict(kind='train_only_memorization_diagnostic', status='running',
                config=asdict(config), class_names=names, records=records,
                source_provenance=source_provenance, steps=steps,
                augmentation=False, schedule='constant', accumulation_steps=1,
                precision='float32', device=str(device), held_out_metric=False)
    def save_meta():
        (out/'diagnostic.json').write_text(json.dumps(meta, indent=2, default=str))
    save_meta()
    history = []
    try:
        model = build_model(config, names).float().to(device)
        meta['resolved_revision'] = getattr(getattr(model.network, 'config', None), '_commit_hash', None)
        optimizer = torch.optim.AdamW([
            dict(params=model.encoder_parameters(), lr=config.encoder_lr),
            dict(params=model.head_parameters(), lr=config.head_lr)], weight_decay=config.weight_decay)
        def measure(step):
            model.eval()
            matrix = np.zeros((len(names), len(names)), dtype=np.int64)
            loss_total = 0.
            with torch.inference_mode():
                for batch in loader:
                    x, y = batch['pixel_values'].to(device), batch['labels'].to(device)
                    logits = model(x)
                    loss_total += segmentation_loss(logits, y, config.ce_weight, config.dice_weight).item()*len(y)
                    matrix += confusion_counts(y.cpu().numpy(), logits.argmax(1).cpu().numpy(), len(names))
            result = metrics_from_confusion(matrix, names)
            result.update(step=step, loss=loss_total/len(records))
            history.append(result)
            (out/'history.json').write_text(json.dumps(history, indent=2, allow_nan=False))
            print(f"step {step}: same-image loss={result['loss']:.4f}, foreground mIoU={result['miou_foreground']}", flush=True)
        measure(0)
        iterator = iter(loader)
        for step in range(1, steps+1):
            try:
                batch = next(iterator)
            except StopIteration:
                iterator = iter(loader)
                batch = next(iterator)
            model.train()
            if config.freeze_batchnorm:
                for module in model.modules():
                    if isinstance(module, torch.nn.modules.batchnorm._BatchNorm):
                        module.eval()
            x, y = batch['pixel_values'].to(device), batch['labels'].to(device)
            optimizer.zero_grad(set_to_none=True)
            loss = segmentation_loss(model(x), y, config.ce_weight, config.dice_weight)
            if not torch.isfinite(loss):
                raise FloatingPointError('Non-finite diagnostic loss')
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), config.max_grad_norm)
            optimizer.step()
            if step % report_every == 0 or step == steps:
                measure(step)
        torch.save({k: v.detach().cpu() for k, v in model.state_dict().items()}, out/'diagnostic_weights.pt')
        (out/'model_config.json').write_text(json.dumps(model.architecture_config, indent=2))
        meta['status'] = 'completed'
        return model.eval(), history
    except BaseException as error:
        meta.update(status='interrupted' if isinstance(error, KeyboardInterrupt) else 'failed',
                    error=f'{type(error).__name__}: {error}')
        raise
    finally:
        save_meta()
