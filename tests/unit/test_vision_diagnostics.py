import json

import numpy as np
import pandas as pd
import pytest
import torch
from PIL import Image

from pipelines.vision import diagnostics as d
from pipelines.vision.training import TrainingConfig, _file_hash


@pytest.fixture
def sample(tmp_path):
    rgb = np.zeros((64, 64, 3), dtype=np.uint8)
    mask = np.zeros((64, 64), dtype=np.uint8)
    mask[20:30, 20:30] = 1
    mask[:8] = 255
    image_path, mask_path = tmp_path/'image.png', tmp_path/'mask.png'
    Image.fromarray(rgb).save(image_path)
    Image.fromarray(mask).save(mask_path)
    return dict(sample_id='sample-1', split='train', image_path=str(image_path),
                mask_path=str(mask_path), image_sha256=_file_hash(image_path),
                mask_sha256=_file_hash(mask_path), pixel_counts=[3484, 100])


class BackgroundModel(torch.nn.Module):
    def forward(self, x):
        out = torch.zeros(len(x), 2, *x.shape[-2:], device=x.device)
        out[:, 0] = 1
        return out


def test_rank_misses_and_test_partition_guard(sample):
    cfg = TrainingConfig(image_size=64)
    scores = d.rank_misses(BackgroundModel(), [sample], cfg, ['background', 'flaking'], 'cpu', ['flaking'])
    row = scores.iloc[0]
    assert row.native_pixels == row.model_pixels == row.missed_as_background == 100
    assert row.recall == 0 and row.missed_as_other_damage == 0
    assert pd.isna(row.precision)
    for split in ('test', 'reserved'):
        with pytest.raises(ValueError, match='Audit only'):
            d.rank_misses(BackgroundModel(), [{**sample, 'split': split}], cfg,
                          ['background', 'flaking'], 'cpu', ['flaking'])


def test_select_misses_balances_classes_deduplicates_and_keeps_erased():
    rows = [dict(sample_id=sid, focus_class=name, recall=recall, erased_by_resize=erased)
            for sid, name, recall, erased in [
                ('same', 'flaking', 0, False), ('same', 'paint-chip', 0, False),
                ('f2', 'flaking', .1, False), ('p2', 'paint-chip', .2, False),
                ('gone', 'paint-chip', None, True), ('good', 'paint-chip', .9, False)]]
    chosen = d.select_misses(pd.DataFrame(rows), ['flaking', 'paint-chip'], count=24)
    assert set(chosen.sample_id) == {'same', 'f2', 'p2', 'gone'}
    assert len(chosen.sample_id.unique()) == len(chosen)
    assert chosen.iloc[1].sample_id == 'gone'


def test_review_must_be_clean_train_and_unchanged(sample):
    review = pd.DataFrame([{**sample, 'review_status': 'clean'}])
    assert d.reviewed_training_records(review, [sample], ['sample-1']) == [sample]
    for status in ('', 'ambiguous', 'class_error'):
        with pytest.raises(ValueError, match='clean review'):
            d.reviewed_training_records(review.assign(review_status=status), [sample], ['sample-1'])
    with pytest.raises(ValueError, match='original training'):
        d.reviewed_training_records(review, [{**sample, 'split': 'val'}], ['sample-1'])
    with pytest.raises(ValueError, match='provenance'):
        d.reviewed_training_records(review.assign(mask_sha256='changed'), [sample], ['sample-1'])
    with pytest.raises(ValueError, match='unique'):
        d.reviewed_training_records(review, [sample], ['sample-1', 'sample-1'])


def test_render_review_full_image_and_zoom(sample):
    import matplotlib.pyplot as plt
    fig = d.render_review(BackgroundModel(), sample, TrainingConfig(image_size=64),
                          ['background', 'flaking'], 'cpu', 'flaking', zoom_size=24)
    assert len(fig.axes) == 6
    assert np.asarray(fig.axes[3].images[0].get_array()).shape == (24, 24, 3)
    plt.close(fig)


class TinyModel(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.encoder = torch.nn.Conv2d(3, 3, 1)
        self.head = torch.nn.Conv2d(3, 2, 1)
        self.network = torch.nn.Identity()
        self.architecture_config = {'fixture': True}
        self.seen_modes = []

    def forward(self, x):
        self.seen_modes.append(self.training)
        return self.head(self.encoder(x))

    def encoder_parameters(self):
        return self.encoder.parameters()

    def head_parameters(self):
        return self.head.parameters()


def test_tiny_fit_same_image_history_provenance_and_artifact_isolation(sample, tmp_path, monkeypatch):
    model = TinyModel()
    monkeypatch.setattr(d, 'build_model', lambda *args: model)
    original = d.SegmentationDataset
    def unaugmented(*args, **kwargs):
        assert kwargs.get('training') is False
        return original(*args, **kwargs)
    monkeypatch.setattr(d, 'SegmentationDataset', unaugmented)
    cfg = TrainingConfig(image_size=64, batch_size=1, weight_decay=0)
    before = model.head.weight.detach().clone()
    out = tmp_path/'diagnostic'
    fitted, history = d.fit_tiny_subset([sample], cfg, ['background', 'flaking'], 'cpu', out,
                                      steps=2, report_every=1, source_provenance={'fixture': True})
    assert [h['step'] for h in history] == [0, 1, 2]
    assert not torch.equal(before, fitted.head.weight)
    assert False in model.seen_modes and True in model.seen_modes
    meta = json.loads((out/'diagnostic.json').read_text())
    assert meta['status'] == 'completed' and meta['held_out_metric'] is False
    assert meta['records'][0]['split'] == 'train'
    assert (out/'diagnostic_weights.pt').is_file()
    with pytest.raises(FileExistsError):
        d.fit_tiny_subset([sample], cfg, ['background', 'flaking'], 'cpu', out, steps=1)
    with pytest.raises(ValueError, match='original training'):
        d.fit_tiny_subset([{**sample, 'split': 'val'}], cfg, ['background', 'flaking'], 'cpu', tmp_path/'bad')


def test_failed_fit_remains_failed(sample, tmp_path, monkeypatch):
    def broken(*args):
        raise RuntimeError('fixture failure')
    monkeypatch.setattr(d, 'build_model', broken)
    out = tmp_path/'failed'
    with pytest.raises(RuntimeError, match='fixture failure'):
        d.fit_tiny_subset([sample], TrainingConfig(image_size=64), ['background', 'flaking'], 'cpu', out, steps=1)
    assert json.loads((out/'diagnostic.json').read_text())['status'] == 'failed'
