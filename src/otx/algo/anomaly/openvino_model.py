"""OTX Anomaly OpenVINO model.

All anomaly models use the same AnomalyDetection model from ModelAPI.
"""

# TODO(someone): Revisit mypy errors after OTXLitModule deprecation and anomaly refactoring
# mypy: ignore-errors

# Copyright (C) 2024 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Sequence

import numpy as np
import torch
from anomalib.metrics import create_metric_collection
from lightning import Callback, Trainer
from torchvision.transforms.functional import resize

from otx.core.data.entity.anomaly import AnomalyClassificationDataBatch
from otx.core.metrics.types import MetricCallable, NullMetricCallable
from otx.core.model.anomaly import AnomalyModelInputs
from otx.core.model.base import OVModel
from otx.core.types.label import AnomalyLabelInfo

if TYPE_CHECKING:
    from anomalib.metrics import AnomalibMetricCollection
    from model_api.models import Model
    from model_api.models.anomaly import AnomalyResult


class _OVMetricCallback(Callback):
    def __init__(self) -> None:
        super().__init__()

    def on_test_start(self, trainer: Trainer, pl_module: AnomalyOpenVINO) -> None:
        pl_module.image_metrics.set_threshold(pl_module.model.image_threshold / pl_module.model.normalization_scale)
        pl_module.pixel_metrics.set_threshold(pl_module.model.pixel_threshold / pl_module.model.normalization_scale)

    def on_test_epoch_start(self, trainer: Trainer, pl_module: AnomalyOpenVINO) -> None:
        pl_module.image_metrics.reset()
        pl_module.pixel_metrics.reset()

    def on_test_batch_end(
        self,
        trainer: Trainer,
        pl_module: AnomalyOpenVINO,
        outputs: list[AnomalyResult],
        batch: AnomalyModelInputs,
        batch_idx: int,
        dataloader_idx: int = 0,
    ) -> None:
        # Convert modelAPI scores to anomaly scores. i.e flip scores with Normal label.
        score_dict = {
            "pred_scores": torch.tensor(
                [output.pred_score if output.pred_label == "Anomaly" else 1 - output.pred_score for output in outputs],
            ),
            "labels": torch.tensor(batch.labels) if batch.batch_size == 1 else torch.vstack(batch.labels),
        }
        if not isinstance(batch, AnomalyClassificationDataBatch):
            score_dict["anomaly_maps"] = torch.tensor(np.array([output.anomaly_map for output in outputs])) / 255.0
            score_dict["masks"] = batch.masks if batch.batch_size == 1 else torch.vstack(batch.masks)
            # resize masks and anomaly maps to 256,256 as this is the size used in Anomalib
            score_dict["masks"] = resize(score_dict["masks"], (256, 256))
            score_dict["anomaly_maps"] = resize(score_dict["anomaly_maps"], (256, 256))

        self._update_metrics(pl_module.image_metrics, pl_module.pixel_metrics, score_dict)

    def on_test_epoch_end(self, trainer: Trainer, pl_module: AnomalyOpenVINO) -> None:
        self._log_metrics(pl_module)

    def _update_metrics(
        self,
        image_metric: AnomalibMetricCollection,
        pixel_metric: AnomalibMetricCollection,
        outputs: dict[str, torch.Tensor],
    ) -> None:
        """Update performance metrics."""
        image_metric.update(outputs["pred_scores"], outputs["labels"].int())
        if "masks" in outputs and "anomaly_maps" in outputs:
            pixel_metric.update(outputs["anomaly_maps"], outputs["masks"].int())

    @staticmethod
    def _log_metrics(pl_module: AnomalyOpenVINO) -> None:
        """Log computed performance metrics."""
        if pl_module.pixel_metrics._update_called:  # noqa: SLF001
            pl_module.log_dict(pl_module.pixel_metrics, prog_bar=True)
            pl_module.log_dict(pl_module.image_metrics, prog_bar=False)
        else:
            pl_module.log_dict(pl_module.image_metrics, prog_bar=True)


class AnomalyOpenVINO(OVModel):
    """Anomaly OpenVINO model."""

    def __init__(
        self,
        model_name: str,
        async_inference: bool = True,
        max_num_requests: int | None = None,
        use_throughput_mode: bool = True,
        model_api_configuration: dict[str, Any] | None = None,
        metric: MetricCallable = NullMetricCallable,  # Metrics is computed using Anomalib's metric
        **kwargs,
    ) -> None:
        super().__init__(
            model_name=model_name,
            model_type="AnomalyDetection",
            async_inference=async_inference,
            max_num_requests=max_num_requests,
            use_throughput_mode=use_throughput_mode,
            model_api_configuration=model_api_configuration,
            metric=metric,
        )
        metric_names = ["AUROC", "F1Score"]
        self.image_metrics: AnomalibMetricCollection = create_metric_collection(metric_names, prefix="image_")
        self.pixel_metrics: AnomalibMetricCollection = create_metric_collection(metric_names, prefix="pixel_")

    def _create_model(self) -> Model:
        from model_api.adapters import OpenvinoAdapter, create_core, get_user_config
        from model_api.models import AnomalyDetection

        plugin_config = get_user_config("AUTO", str(self.num_requests), "AUTO")
        if self.use_throughput_mode:
            plugin_config["PERFORMANCE_HINT"] = "THROUGHPUT"

        model_adapter = OpenvinoAdapter(
            create_core(),
            self.model_name,
            max_num_requests=self.num_requests,
            plugin_config=plugin_config,
        )
        return AnomalyDetection.create_model(
            model=model_adapter,
            model_type=self.model_type,
            configuration=self.model_api_configuration,
        )

    def configure_callbacks(self) -> Sequence[Callback] | Callback:
        """Return the metric callback."""
        return _OVMetricCallback()

    def test_step(self, inputs: AnomalyModelInputs, batch_idx: int) -> list[AnomalyResult]:
        """Return outputs from the OpenVINO model."""
        return self.forward(inputs)  # type: ignore[return-value]

    def predict_step(self, inputs: AnomalyModelInputs, batch_idx: int) -> list[AnomalyResult]:
        """Return outputs from the OpenVINO model."""
        return self.forward(inputs)  # type: ignore[return-value]

    def _customize_outputs(self, outputs: list[AnomalyResult], inputs: AnomalyModelInputs) -> list[AnomalyResult]:
        """Return outputs from the OpenVINO model as is."""
        return outputs

    def _create_label_info_from_ov_ir(self) -> AnomalyLabelInfo:
        return AnomalyLabelInfo()
